"""Pull notifications are claimed once and only acknowledged delivery advances the flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from api.deps import get_current_user
from api.v1.daily_pipeline import get_notification_consumption_service, router
from daily_pipeline import notification_consumption as module
from daily_pipeline.notifications import notification_content
from fastapi import FastAPI
from infrastructure.core.security import encrypt_api_key


def repository():
    execution = SimpleNamespace(
        id=27, node_id=20, source="auto", status="waiting", result_data=None,
        request_data={"delivery_mode": "pull", "ready": True, "delivery_started": False,
                      "github_name": "author", "recipient_id": 1, "content": "Daily test notification"},
    )
    node = SimpleNamespace(id=20, failure_id=10, previous_node_id=None,
                           stage_code="problem_pr_notification", status="waiting")
    failure = SimpleNamespace(id=10, processing_status="running")
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), refresh=AsyncMock())
    return SimpleNamespace(
        db=db, candidate_failures=AsyncMock(return_value=[10]), lock_failure=AsyncMock(return_value=failure),
        nodes=AsyncMock(return_value=[node]), latest_execution=AsyncMock(return_value=execution),
        standalone_delivery=AsyncMock(return_value=None),
        execution=AsyncMock(return_value=execution), node=AsyncMock(return_value=node),
        recipient=AsyncMock(return_value=SimpleNamespace(id=1, name="Test User", account=encrypt_api_key("employee-test-only"))),
    )


@pytest.mark.asyncio
async def test_claim_is_durable_and_cannot_be_claimed_again():
    repo = repository()
    service = module.DailyNotificationConsumptionService(repo)
    item = await service.claim("consumer")
    assert item["githubName"] == "author"
    assert "receiver" not in item
    assert item["content"] == "Daily test notification"
    data = repo.latest_execution.return_value.request_data
    assert data["receipt_hash"] == module.receipt_digest(item["receiptToken"])
    assert item["receiptToken"] not in str(data)
    assert "employee-test-only" not in str(data)
    repo.db.commit.assert_awaited_once()
    assert await service.claim("consumer") is None


@pytest.mark.asyncio
async def test_unavailable_lock_is_skipped_and_server_mapping_is_not_required():
    repo = repository()
    service = module.DailyNotificationConsumptionService(repo)
    repo.lock_failure.return_value = None
    assert await service.claim("consumer") is None
    repo.recipient.assert_not_awaited()
    repo.lock_failure.return_value = SimpleNamespace(id=10, processing_status="running")
    repo.recipient.return_value = None
    assert (await service.claim("consumer"))["githubName"] == "author"
    repo.recipient.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_and_superseded_flows_are_never_claimed():
    repo = repository()
    repo.lock_failure.return_value.processing_status = "ended"
    assert await module.DailyNotificationConsumptionService(repo).claim("consumer") is None
    repo.latest_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_deadline_notice_can_finish_after_the_business_flow_resolves():
    repo = repository()
    repo.lock_failure.return_value.processing_status = "resolved"
    execution = repo.execution.return_value
    execution.request_data["purpose"] = "revert_deadline"
    repo.standalone_delivery.return_value = execution
    service = module.DailyNotificationConsumptionService(repo)
    item = await service.claim("consumer")
    await service.acknowledge(27, item["receiptToken"], "sent", "consumer")
    assert execution.status == "completed"
    assert execution.result_data["delivery_status"] == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["sent", "failed"])
async def test_valid_result_uses_existing_flow_transition(monkeypatch, outcome):
    repo = repository()
    service = module.DailyNotificationConsumptionService(repo)
    item = await service.claim("consumer")
    finish = AsyncMock()
    monkeypatch.setattr(module, "DailyTrackingService", lambda db: SimpleNamespace(finish_node=finish))
    result = await service.acknowledge(27, item["receiptToken"], outcome, "consumer")
    assert result["alreadyProcessed"] is False
    assert finish.await_args.args[0:2] == (27, outcome == "sent")
    assert finish.await_args.args[2]["delivery_status"] == outcome


@pytest.mark.asyncio
async def test_unknown_delivery_stays_waiting_and_is_not_redelivered(monkeypatch):
    repo = repository()
    service = module.DailyNotificationConsumptionService(repo)
    item = await service.claim("consumer")
    finish = AsyncMock()
    monkeypatch.setattr(module, "DailyTrackingService", lambda db: SimpleNamespace(finish_node=finish))
    await service.acknowledge(27, item["receiptToken"], "unknown", "consumer")
    assert repo.execution.return_value.status == "waiting"
    assert await service.claim("consumer") is None
    finish.assert_not_awaited()
    # A consumer can later confirm an uncertain delivery using the same receipt.
    await service.acknowledge(27, item["receiptToken"], "sent", "consumer")
    finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_owner_and_stale_callback_guards(monkeypatch):
    repo = repository()
    service = module.DailyNotificationConsumptionService(repo)
    item = await service.claim("consumer")
    finish = AsyncMock()
    monkeypatch.setattr(module, "DailyTrackingService", lambda db: SimpleNamespace(finish_node=finish))
    for receipt, owner in [("wrong", "consumer"), (item["receiptToken"], "other")]:
        with pytest.raises(ValueError, match="领取凭据无效"):
            await service.acknowledge(27, receipt, "sent", owner)
    repo.lock_failure.return_value.processing_status = "ended"
    with pytest.raises(ValueError, match="已失效"):
        await service.acknowledge(27, item["receiptToken"], "sent", "consumer")
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_acknowledgements_are_idempotent(monkeypatch):
    repo = repository()
    service = module.DailyNotificationConsumptionService(repo)
    item = await service.claim("consumer")
    execution = repo.execution.return_value
    execution.status = "completed"
    execution.result_data = {"delivery_status": "sent"}
    finish = AsyncMock()
    monkeypatch.setattr(module, "DailyTrackingService", lambda db: SimpleNamespace(finish_node=finish))
    assert (await service.acknowledge(27, item["receiptToken"], "sent", "consumer"))["alreadyProcessed"]
    with pytest.raises(ValueError, match="不能更改"):
        await service.acknowledge(27, item["receiptToken"], "failed", "consumer")
    finish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role, expected", [("user", 403), ("admin", 200)])
async def test_consumer_api_authorization_and_no_cache(role, expected):
    app = FastAPI()
    app.include_router(router)
    service = SimpleNamespace(claim=AsyncMock(return_value=None), acknowledge=AsyncMock(return_value={"id":"27","result":"sent","alreadyProcessed":False}))
    async def user():
        return SimpleNamespace(username="consumer", role=role)
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_notification_consumption_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/daily-pipeline/notifications/claim")
        assert response.status_code == expected
        if expected == 200:
            assert response.json() is None
            assert response.headers["cache-control"] == "no-store"
            service.claim.assert_awaited_once_with("consumer")
        else:
            service.claim.assert_not_awaited()
        response = await client.get("/daily-pipeline/notifications/27/result", params={"result":"sent"}, headers={"X-Notification-Receipt":"x"*43})
        assert response.status_code == expected
        if expected == 200:
            assert response.headers["cache-control"] == "no-store"
        else:
            service.acknowledge.assert_not_awaited()


@pytest.mark.asyncio
async def test_result_get_requires_receipt_and_valid_outcome():
    app = FastAPI()
    app.include_router(router)
    service = SimpleNamespace(claim=AsyncMock(return_value=None), acknowledge=AsyncMock())
    async def user():
        return SimpleNamespace(username="consumer", role="admin")
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_notification_consumption_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/daily-pipeline/notifications/27/result?result=sent")
        assert response.status_code == 422
        response = await client.get("/daily-pipeline/notifications/27/result?result=invalid", headers={"X-Notification-Receipt":"x"*43})
        assert response.status_code == 422
        response = await client.post("/daily-pipeline/notifications/27/result")
        assert response.status_code == 405
        response = await client.post("/daily-pipeline/notifications/claim")
        assert response.status_code == 405
        service.acknowledge.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumption_records_are_paginated_and_hide_credentials():
    row = SimpleNamespace(id=27, status="waiting", started_at=None, finished_at=None,
        request_data={"github_name":"author", "recipient_name":"Test User", "content":"message", "claimed_by":"consumer", "claimed_at":"2026-11-17T12:00:00Z", "receipt_hash":"private-receipt-hash", "receiver":"private-account"},
        result_data={"delivery_status":"unknown", "confirmed_at":"2026-11-17T12:01:00Z"})
    repo = SimpleNamespace(db=SimpleNamespace(), consumption_records=AsyncMock(return_value=([(row, "problem_pr_notification")],1)))
    result = await module.DailyNotificationConsumptionService(repo).records(2,5,"unknown")
    repo.consumption_records.assert_awaited_once_with(2,5,"unknown",None)
    assert result["page"] == 2 and result["pageSize"] == 5 and result["total"] == 1
    record = result["items"][0]
    assert record["result"] == "unknown" and record["name"] == "Test User"
    assert record["confirmedAt"] == "2026-11-17T12:01:00Z"
    assert "private-receipt-hash" not in str(result) and "private-account" not in str(result)
    assert "receiptToken" not in record and "receiver" not in record
    row.status = "ended"
    assert (await module.DailyNotificationConsumptionService(repo).records())["items"][0]["result"] == "ended"


@pytest.mark.parametrize("claimed,outcome,node_status,expected", [
    (False,None,"waiting","pending"),
    (True,None,"waiting","processing"),
    (True,"unknown","waiting","processing"),
    (True,"sent","completed","completed"),
    (True,"failed","failed","completed"),
    (False,None,"ended","ended"),
])
def test_notification_lifecycle_status(claimed, outcome, node_status, expected):
    row = SimpleNamespace(status=node_status, request_data={"delivery_started":claimed}, result_data={"delivery_status":outcome})
    assert module.consumption_status(row) == expected


def test_notification_links_the_actual_failed_job_in_its_source_workflow():
    source = SimpleNamespace(name="failed-case", hardware="A3", source_run_id=222, source_id=333)
    run = SimpleNamespace(repository="example/source", run_id=111)
    issue = SimpleNamespace(repository="example/source", number=444)
    content = notification_content(source, run, issue, "https://github.com/example/source/pull/1", "https://github.com/example/source/pull/2", "author")
    assert "失败 Job 流水线：https://github.com/example/source/actions/runs/222/job/333" in content
    assert "Daily 主流水线：https://github.com/example/source/actions/runs/111" in content
    assert "下一轮 Daily 执行前 30 分钟完成修复并合入" in content
    assert "failed-case (A3) solved by PR #编号" in content
    assert "将通知原问题 PR 合入人处理回退 PR" in content


def test_notification_missing_job_id_keeps_a_valid_workflow_link():
    source = SimpleNamespace(name="failed-case", hardware="CPU", source_run_id=None, source_id=None)
    run = SimpleNamespace(repository="example/source", run_id=111)
    content = notification_content(source, run, None, "original", "revert", "author")
    assert "/actions/runs/111" in content and "/job/None" not in content
    assert "Issue：" not in content
