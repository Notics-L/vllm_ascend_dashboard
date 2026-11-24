"""Human completion advances only the current node and preserves callback guards."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from api.deps import get_current_user
from api.v1.daily_pipeline import router
from daily_pipeline.revert import execute_revert
from daily_pipeline.tracking import DailyTrackingService, ManualInputError, current_node
from infrastructure.db.base import get_db
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution


def objects(stage="classify", status="pending", category=None):
    failure = SimpleNamespace(id=1, issue_id=None, processing_status="pending", problem_category=category, ended_at=None)
    node = SimpleNamespace(id=10, failure_id=1, stage_code=stage, status=status, previous_node_id=None,
                           started_at=None, next_run_at=None, finished_at=None, updated_at=None)
    added = []
    async def flush():
        for index, item in enumerate(added, 20):
            if item.id is None:
                item.id = index
    db = SimpleNamespace(get=AsyncMock(return_value=node), scalar=AsyncMock(side_effect=[failure, 0]),
                         scalars=AsyncMock(return_value=[node]), execute=AsyncMock(), refresh=AsyncMock(),
                         add=added.append, flush=AsyncMock(side_effect=flush), commit=AsyncMock())
    return failure, node, db, added


@pytest.fixture(autouse=True)
def no_catalog_writes(monkeypatch):
    monkeypatch.setattr("daily_pipeline.tracking.reconcile_case_availability", AsyncMock())
    monkeypatch.setattr("daily_pipeline.tracking.start_processing", AsyncMock())
    monkeypatch.setattr("daily_pipeline.tracking.read_settings", AsyncMock(return_value={"repository": "example/source"}))


@pytest.mark.asyncio
async def test_manual_classification_advances_to_locating_and_records_actor():
    failure, node, db, added = objects()
    result = await DailyTrackingService(db).manual_complete(10, True, {"category": "code"}, "", "alice")
    execution, locating = added
    assert execution.source == "manual" and execution.operator == "alice"
    assert execution.attempt_no == 1 and execution.message == "代码问题"
    assert execution.status == node.status == "completed"
    assert locating.stage_code == "locating" and locating.previous_node_id == node.id
    assert failure.problem_category == "code" and result["status"] == "running"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("mode", [None, "auto", "manual", "optimize"])
@pytest.mark.asyncio
async def test_locating_selects_only_the_saved_business_path(mode):
    failure, node, db, added = objects("locating", category="code")
    inputs = {"pr_number": 123}
    if mode is not None:
        inputs["revert_mode"] = mode
    service = DailyTrackingService(db)
    with patch.object(service, "_queue_processing", new_callable=AsyncMock) as queue:
        queue.return_value = True
        response = await service.manual_complete(10, True, inputs, "", "alice")
    locating, *steps = added
    assert failure.processing_status == "running"
    assert locating.result_data["revert_mode"] == (mode or "auto")
    assert steps[0].previous_node_id == node.id
    if mode == "optimize":
        notice, merge = steps
        assert [notice.stage_code, merge.stage_code] == ["problem_pr_notification", "pr_merge"]
        assert merge.previous_node_id == notice.id
        assert response["automaticQueued"] is True
        queue.assert_awaited_once_with(notice.id)
        return
    revert, notice, merge = steps
    assert [revert.stage_code, notice.stage_code, merge.stage_code] == [
        "revert_pr", "problem_pr_notification", "pr_merge",
    ]
    if mode == "manual":
        assert revert.status == "waiting" and response["automaticQueued"] is None
        queue.assert_not_awaited()
    else:
        assert revert.status == "pending" and response["automaticQueued"] is True
        queue.assert_awaited_once_with(revert.id)


@pytest.mark.parametrize("number", [123, 456])
@pytest.mark.asyncio
async def test_user_can_register_a_distinct_revert_pr(number):
    failure, node, db, added = objects("revert_pr", status="waiting", category="code")
    node.previous_node_id = 9
    locating = SimpleNamespace(id=9, status="completed", previous_node_id=None)
    notice = SimpleNamespace(id=11, stage_code="problem_pr_notification", status="pending")
    db.scalars.return_value = [locating, node]
    db.scalar.side_effect = [failure, {"pr_number": 123, "revert_mode": "manual"}, 0, notice]
    service = DailyTrackingService(db)
    with patch.object(service, "_queue_processing", new_callable=AsyncMock) as queue:
        if number == 123:
            with pytest.raises(ManualInputError, match="不能是同一个"):
                await service.manual_complete(10, True, {"pr_number": number}, "", "alice")
            assert not added
            db.commit.assert_not_awaited()
            queue.assert_not_awaited()
            return
        queue.return_value = True
        await service.manual_complete(10, True, {"pr_number": number}, "", "alice")
    assert node.status == "completed"
    assert added[0].result_data["original_pr_url"] == "https://github.com/example/source/pull/123"
    assert added[0].result_data["pr_url"] == "https://github.com/example/source/pull/456"
    queue.assert_awaited_once_with(notice.id)


@pytest.mark.asyncio
async def test_manual_revert_is_never_created_by_the_worker():
    failure, node, db, _ = objects("revert_pr", status="pending", category="code")
    node.previous_node_id = 9
    locating = SimpleNamespace(id=9, status="completed", previous_node_id=None)
    db.scalars.return_value = [locating, node]
    db.scalar.side_effect = [failure, {"revert_mode": "manual"}]
    with patch("daily_pipeline.revert.DailyRevertClient") as client:
        assert await execute_revert(db, node.id)
        client.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("note", ["", "  节点连接中断，已恢复  "])
@pytest.mark.asyncio
async def test_environment_classification_is_resolved_and_records_automatic_success(note):
    failure, node, db, added = objects()
    result = await DailyTrackingService(db).manual_complete(
        10, True, {"category": "environment"}, note, "alice"
    )
    manual, mark, automatic = added
    assert manual.status == node.status == "completed"
    assert manual.message == "环境问题"
    assert manual.result_data.get("note", "") == note.strip()
    assert mark.stage_code == "mark_success" and mark.previous_node_id == node.id
    assert automatic.source == "auto" and automatic.node_id == mark.id
    assert mark.status == automatic.status == "completed"
    assert failure.ended_at == mark.finished_at == automatic.finished_at
    assert failure.problem_category == "environment" and result["status"] == "resolved"
    assert result["automaticQueued"] is None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_automatic_environment_classification_uses_existing_issue_completion():
    failure, node, db, added = objects(status="running")
    failure.processing_status, failure.issue_id = "running", 11
    classification = SimpleNamespace(node_id=node.id, status="running")
    db.get.side_effect = [classification, node]
    db.scalar.side_effect = [failure, 123]
    service = DailyTrackingService(db)
    close_issue = AsyncMock()
    with patch.object(service, "close_resolved_issue", close_issue):
        result = await service.finish_node(99, True, {"category": "environment"}, "环境问题")
    assert result == {"status": "resolved", "automaticQueued": None}
    assert added[0].stage_code == "mark_success" and added[1].source == "auto"
    close_issue.assert_awaited_once_with(11, 123)


@pytest.mark.parametrize("stage", ["locating", "mark_success"])
@pytest.mark.asyncio
async def test_legacy_environment_node_is_completed_once(stage):
    failure, node, db, added = objects(stage, status="waiting", category="environment")
    failure.processing_status = "running"
    db.scalar.side_effect = [failure, 2, None] if stage == "mark_success" else [failure, 2]
    service = DailyTrackingService(db)
    assert await service.complete_environment_node(node.id)
    assert failure.processing_status == "resolved" and node.status == "completed"
    assert added[0].source == "auto" and added[0].attempt_no == 3
    assert len(added) == (3 if stage == "locating" else 1)
    assert db.execute.await_args.args[0].compile().params["status"] == "ended"
    db.scalar.side_effect = [failure]
    assert await service.complete_environment_node(node.id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_environment_recovery_does_not_complete_other_categories():
    _, node, db, added = objects("locating", category="code")
    assert await DailyTrackingService(db).complete_environment_node(node.id)
    assert not added and node.status == "pending"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_retry_uses_next_attempt_and_ends_old_auto_execution():
    failure, node, db, added = objects("pr_merge", "waiting", "code")
    failure.processing_status = "running"
    db.scalar.side_effect = [failure, 2, None]
    await DailyTrackingService(db).manual_complete(10, True, {"pr_number": 123}, "PR 已合入", "alice")
    assert added[0].attempt_no == 3
    assert failure.processing_status == "resolved"
    assert node.next_run_at is None
    # The old execution is ended before creating the new audited attempt.
    statement = db.execute.await_args.args[0]
    assert statement.table.name == Execution.__tablename__
    assert statement.compile().params["status"] == "ended"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("failure_status,node_status", [
    ("ended", "pending"), ("resolved", "completed"), ("running", "completed"), ("ended", "ended"),
])
@pytest.mark.asyncio
async def test_ended_or_completed_nodes_cannot_be_changed(failure_status, node_status):
    failure, _, db, added = objects(status=node_status)
    failure.processing_status = failure_status
    with pytest.raises(ValueError, match="当前未完成"):
        await DailyTrackingService(db).manual_complete(10, True, {"category": "code"}, "理由", "alice")
    assert not added
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_future_node_cannot_skip_its_predecessor():
    _, node, db, _ = objects()
    node.previous_node_id = 9
    with pytest.raises(ValueError, match="当前未完成"):
        await DailyTrackingService(db).manual_complete(10, True, {"category": "code"}, "理由", "alice")
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("stage,result", [("classify", {}), ("classify", {"category": "other"}),
    ("revert_pr", {}), ("pr_merge", {"pr_number": True}), ("image_rebuild", {"run_id": 0})])
@pytest.mark.asyncio
async def test_invalid_business_result_is_not_saved(stage, result):
    _, _, db, added = objects(stage)
    with pytest.raises(ManualInputError):
        await DailyTrackingService(db).manual_complete(10, True, result, "理由", "alice")
    assert not added
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_result_can_be_retried_without_resolving_case():
    failure, node, db, _ = objects("mark_success", "waiting", "environment")
    await DailyTrackingService(db).manual_complete(10, False, {}, "环境未恢复", "alice")
    assert failure.processing_status == "running" and failure.ended_at is None
    assert node.status == "failed" and current_node([node]) is node


@pytest.mark.asyncio
async def test_version_classification_uses_new_flow_and_legacy_nodes_cannot_close_it():
    failure, _, db, added = objects()
    await DailyTrackingService(db).manual_complete(10, True, {"category": "version"}, "", "alice")
    assert failure.problem_category == "version" and not added[1:]
    _, _, db, _ = objects("image_rebuild", "waiting", "version")
    with pytest.raises(ManualInputError, match="版本配套流程"):
        await DailyTrackingService(db).manual_complete(10, True, {"run_id": 10}, "构建成功", "alice")


@pytest.mark.asyncio
async def test_stale_automatic_callback_cannot_overwrite_human_result():
    failure, node, db, _ = objects("pr_merge", "completed", "code")
    failure.processing_status = "resolved"
    old = SimpleNamespace(node_id=node.id, status="ended")
    db.get.side_effect = [old, node]
    db.scalar.side_effect = [failure]
    with pytest.raises(ValueError, match="回调已失效"):
        await DailyTrackingService(db).finish_node(5, False, {}, "迟到的自动结果")
    assert old.status == "ended" and node.status == "completed"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_classification_cannot_change_during_locating():
    _, _, db, _ = objects("locating", category="code")
    with pytest.raises(ManualInputError, match="分类不能修改"):
        await DailyTrackingService(db).manual_complete(10, True, {"category": "environment"}, "理由", "alice")
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_api_rejects_forged_actor_and_readonly_user():
    app = FastAPI()
    app.include_router(router)
    role = "admin"
    async def user():
        return SimpleNamespace(username="real-user", role=role)
    async def database():
        yield SimpleNamespace()
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = {"message": "人工分类", "result": {"category": "code"}, "operator": "forged"}
        response = await client.post("/daily-pipeline/processing/nodes/10/manual-complete", json=body)
        assert response.status_code == 422
        role = "user"
        body.pop("operator")
        response = await client.post("/daily-pipeline/processing/nodes/10/manual-complete", json=body)
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_api_accepts_category_only_and_uses_authenticated_actor():
    app = FastAPI()
    app.include_router(router)
    db = SimpleNamespace()
    async def user():
        return SimpleNamespace(username="real-user", role="admin")
    async def database():
        yield db
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = database
    with patch.object(DailyTrackingService, "manual_complete", new_callable=AsyncMock) as complete:
        complete.return_value = {"status": "running", "executionId": 1}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/daily-pipeline/processing/nodes/10/manual-complete", json={"result": {"category": "code"}})
        assert response.status_code == 200
        complete.assert_awaited_once_with(10, True, {"category": "code"}, "", "real-user")


@pytest.mark.asyncio
async def test_manual_failure_accepts_an_empty_reason():
    _, node, db, added = objects("problem_pr_notification", category="code")
    await DailyTrackingService(db).manual_complete(10, False, {}, "  ", "alice")
    assert node.status == added[0].status == "failed"
    assert added[0].message == ""
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("mode", [None, "manual"])
@pytest.mark.asyncio
async def test_revert_retry_respects_the_selected_creation_mode(mode):
    failure, node, db, _ = objects("revert_pr", status="failed", category="code")
    previous = SimpleNamespace(id=9, status="completed", previous_node_id=None)
    node.previous_node_id = 9
    db.scalars.return_value = [previous, node]
    db.scalar.side_effect = [failure, {"revert_mode": mode} if mode else None]
    service = DailyTrackingService(db)
    if mode == "manual":
        with pytest.raises(ValueError, match="请登记用户创建的回退 PR"):
            await service.retry_processing(10)
        assert node.status == "failed"
        db.commit.assert_not_awaited()
    else:
        result = await service.retry_processing(10)
        assert result == {"queued": True}
        assert node.status == "pending" and failure.processing_status == "running"
        db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_attempt_failure_keeps_business_flow_active():
    failure, node, db, added = objects("problem_pr_notification", "running", "code")
    failure.processing_status = "running"
    await DailyTrackingService(db).manual_complete(10, False, {}, "通知服务暂时不可达", "alice")
    assert node.status == "failed"
    assert added[0].status == "failed"
    assert failure.processing_status == "running"
    assert failure.ended_at is None
    assert current_node([node]) is node
