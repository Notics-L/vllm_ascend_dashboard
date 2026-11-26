from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daily_pipeline import version_resolution as module

NOW = datetime(2026, 11, 19, 4)
TARGET = {
    "repository": "project/repository",
    "workflow": "build.yaml",
    "trigger_url": "https://github.com/project/repository/actions/workflows/build.yaml",
}
BUILD_URL = "https://github.com/project/repository/actions/runs/123"


def member(identity, flow_id=20, issue_id=7):
    return SimpleNamespace(
        id=identity,
        version_flow_id=flow_id,
        issue_id=issue_id,
        node_id=11,
        problem_category="version",
        processing_status="running",
    )


def flow(status, resolution=None):
    return SimpleNamespace(
        id=20,
        status=status,
        resolution=resolution,
        build=None,
        ready_at=NOW,
        notified_at=None,
        updated_at=NOW,
    )


def setup(monkeypatch, subject, members):
    issue = SimpleNamespace(
        id=7,
        state="open",
        deleted_at=None,
        run_id=100,
        number=43,
        repository=TARGET["repository"],
    )
    source = SimpleNamespace(run_id=100)
    run = SimpleNamespace(
        run_id=100, repository=TARGET["repository"], source_branch="main"
    )
    models = {module.Issue: issue, module.SourceNode: source, module.Run: run}
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _: models[model]),
        scalar=AsyncMock(return_value=subject),
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    tracking = SimpleNamespace(
        db=db, _client=AsyncMock(return_value=client), close_resolved_issue=AsyncMock()
    )
    monkeypatch.setattr(module, "active_members", AsyncMock(return_value=members))
    monkeypatch.setattr(module, "build_target", AsyncMock(return_value=TARGET))
    monkeypatch.setattr(module, "reconcile_case_availability", AsyncMock())
    monkeypatch.setattr(module, "now", lambda: NOW)
    return tracking, client


@pytest.mark.parametrize("data", [{}, {"message": "  依赖恢复  "}])
@pytest.mark.asyncio
async def test_external_confirmation_generates_manual_build_notice(monkeypatch, data):
    subject = flow("waiting_resolution")
    first = member(1)
    tracking, _ = setup(monkeypatch, subject, [first])
    monkeypatch.setattr(
        module,
        "lock_failures_and_flows",
        AsyncMock(return_value=([first], {20: subject})),
    )
    notice = AsyncMock()
    monkeypatch.setattr(module, "enqueue_build_notice", notice)
    await module.submit_version_action(
        tracking, 1, "external_recovered", "maintainer", data
    )
    assert subject.status == "waiting_build"
    assert subject.resolution["build_target"] == TARGET
    assert subject.resolution["confirmed_by"] == "maintainer"
    assert subject.resolution["note"] == data.get("message", "").strip()
    assert first.processing_status == "running"
    notice.assert_awaited_once_with(tracking.db, subject)
    tracking._client.assert_not_awaited()


@pytest.mark.asyncio
async def test_pr_merge_advances_to_manual_build_without_resolving(monkeypatch):
    subject = flow(
        "waiting_pr",
        {
            "references": {
                "repair": {
                    "pr_repository": TARGET["repository"],
                    "pr_number": 54,
                    "pr_url": "https://github.com/project/repository/pull/54",
                }
            }
        },
    )
    first = member(1)
    tracking, client = setup(monkeypatch, subject, [first])
    notice = AsyncMock()
    monkeypatch.setattr(module, "enqueue_build_notice", notice)
    client.pull_request.return_value = {"merged": False}
    await module.advance_version_flow(tracking, 20)
    assert subject.status == "waiting_pr"
    notice.assert_not_awaited()
    client.pull_request.return_value = {
        "merged": True,
        "merged_at": "2026-11-19T03:45:00Z",
    }
    await module.advance_version_flow(tracking, 20)
    assert subject.status == "waiting_build"
    assert subject.resolution["merged_kind"] == "repair"
    assert first.processing_status == "running"
    client.run.assert_not_awaited()
    notice.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("conclusion", ["success", "failure"])
async def test_valid_build_link_propagates_result_only_to_members(
    monkeypatch, conclusion
):
    subject = flow("waiting_build", {"build_target": TARGET})
    members = [member(1), member(2)]
    tracking, client = setup(monkeypatch, subject, members)
    monkeypatch.setattr(
        module,
        "lock_failures_and_flows",
        AsyncMock(return_value=([members[0]], {20: subject})),
    )
    client.run.return_value = {
        "path": ".github/workflows/build.yaml@main",
        "created_at": "2026-11-19T03:59:00Z",
        "status": "in_progress",
    }
    await module.submit_version_action(
        tracking, 1, "track_build", "maintainer", {"build_url": BUILD_URL}
    )
    assert subject.status == "building"
    await module.advance_version_flow(tracking, 20)
    assert [row.processing_status for row in members] == ["running"] * 2
    client.run.return_value.update(status="completed", conclusion=conclusion)
    await module.advance_version_flow(tracking, 20)
    assert subject.build["url"] == BUILD_URL
    expected = "resolved" if conclusion == "success" else "unresolved"
    assert [row.processing_status for row in members] == [expected] * 2
    assert tracking.close_resolved_issue.await_count == (conclusion == "success")


@pytest.mark.asyncio
async def test_sharing_is_explicit_and_cannot_split_existing_group(monkeypatch):
    target = flow("waiting_resolution")
    first, second, unselected = member(1), member(2, None), member(3, 30)
    tracking, _ = setup(monkeypatch, target, [first, second])
    monkeypatch.setattr(
        module,
        "lock_failures_and_flows",
        AsyncMock(return_value=([first, second], {20: target})),
    )
    await module.share_version_flow(tracking.db, {1, 2}, 1)
    assert [row.version_flow_id for row in (first, second, unselected)] == [20, 20, 30]

    monkeypatch.setattr(
        module,
        "lock_failures_and_flows",
        AsyncMock(
            return_value=(
                [first, unselected],
                {20: target, 30: SimpleNamespace(id=30, status="waiting_resolution")},
            )
        ),
    )
    tracking.db.scalar.return_value = 2
    with pytest.raises(ValueError, match="不能拆分"):
        await module.share_version_flow(tracking.db, {1, 3}, 3)

    unselected.issue_id = 9
    with pytest.raises(ValueError, match="同一个 Issue"):
        await module.share_version_flow(tracking.db, {1, 3}, 3)


def test_only_configured_build_workflow_counts():
    with pytest.raises(ValueError, match="仓库"):
        module.parse_build_run(
            "https://github.com/another/repo/actions/runs/123", TARGET["repository"]
        )
    with pytest.raises(ValueError, match="工作流"):
        module.verify_build_run({"path": ".github/workflows/tests.yaml"}, TARGET)


def test_build_validation_accepts_a_run_without_creation_time():
    module.verify_build_run({"path": ".github/workflows/build.yaml"}, TARGET)


@pytest.mark.asyncio
async def test_build_notice_names_the_shared_flow_and_is_queued_once(monkeypatch):
    subject = flow("waiting_build", {"build_target": TARGET})
    tracking, _ = setup(monkeypatch, subject, [member(1), member(2)])
    tracking.db.scalar.side_effect = [SimpleNamespace(id=5), 0]
    notices = []
    monkeypatch.setattr(
        module,
        "failure_notification_recipients",
        AsyncMock(return_value=["watcher", "watcher"]),
    )
    monkeypatch.setattr(
        module, "add_failure_notification", lambda *args, **kwargs: notices.append(args)
    )
    await module.enqueue_build_notice(tracking.db, subject)
    await module.enqueue_build_notice(tracking.db, subject)
    assert len(notices) == 1
    assert TARGET["trigger_url"] in notices[0][4]
    assert "2 条失败用例共用" in notices[0][4]


def test_known_nightly_workflow_requires_a_real_build_for_affected_hardware():
    a3 = SimpleNamespace(hardware="A3-560T")
    jobs = [
        {
            "name": "Build nightly-a3 (ubuntu) / Build nightly-a3-ubuntu-arm64",
            "conclusion": "success",
        }
    ]
    assert module.missing_nightly_images(jobs, [a3]) is None
    assert "a5" in module.missing_nightly_images(jobs, [SimpleNamespace(hardware="a5")])
    assert module.missing_nightly_images([], [a3])
