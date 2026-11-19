from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from daily_pipeline import code_resolution as module
from infrastructure.clients.daily_pipeline_collection import CollectionSourceError


def make_source(version="main"):
    return SimpleNamespace(
        name="cpu-0 card-(part 1-1)", hardware="CPU", vllm_version=version
    )


def make_comment(label, pr=23, login="developer", association="NONE"):
    return {
        "id": 9,
        "body": f"{label} solved by PR #{pr}",
        "user": {"login": login},
        "author_association": association,
    }


def make_pr(number, merged=False, branch="main", merged_at=None):
    return {
        "number": number,
        "merged": merged,
        "merged_at": merged_at,
        "base": {"ref": branch},
        "state": "closed" if merged else "open",
        "head": {"sha": "revert-head"},
        "user": {"login": "developer"},
        "merged_by": {"login": "reviewer"} if merged else None,
    }


def test_deadline_uses_next_cron_occurrence_in_the_schedule_timezone():
    schedule = ("0 8,12,16 * * *", "Asia/Shanghai")
    assert module.deadline_for_run(schedule, datetime(2026, 11, 19, 0), 30) == datetime(
        2026, 11, 19, 3, 30
    )
    assert module.deadline_for_run(None, datetime(2026, 11, 19, 0), 30) is None


def test_comments_match_only_one_case_and_reject_cross_repository_links():
    main = make_source()
    release = make_source("release")
    labels = module.labels_for_cases([(1, main), (2, release)])
    comments = [
        make_comment(main.name),
        make_comment(f"{release.name} (CPU, release)", pr=24),
        {
            "body": f"{main.name} (main) solved by PR https://github.com/other/repo/pull/7"
        },
        make_comment(f"{main.name} (main)", pr=25),
    ]
    references = module.fix_references(comments, labels, "repo/test")
    assert [ref for _, ref in references[1]] == [
        {
            "pr_number": 25,
            "pr_repository": "repo/test",
            "pr_url": "https://github.com/repo/test/pull/25",
        }
    ]
    assert [ref["pr_number"] for _, ref in references[2]] == [24]


@pytest.mark.asyncio
async def test_unrelated_comment_cannot_mark_the_case_fixed():
    source = make_source()
    refs = module.fix_references(
        [make_comment(source.name, login="outsider")],
        module.labels_for_cases([(1, source)]),
        "repo/test",
    )
    client = SimpleNamespace(
        pull_request=AsyncMock(
            return_value=make_pr(23, True, merged_at="2026-11-19T03:10:00Z")
        )
    )
    repair = await module.latest_repair(
        client,
        refs[1],
        {"author": "developer"},
        make_pr(1, True, merged_at="2026-11-19T02:00:00Z"),
        make_pr(2),
    )
    assert repair is None


def make_flow(repair, *, revert_merged=False, existing=None):
    source = make_source()
    issue = SimpleNamespace(
        id=2, run_id=5, repository="repo/test", number=42, state="open", deleted_at=None
    )
    failure = SimpleNamespace(
        id=1,
        node_id=4,
        issue_id=issue.id,
        problem_category="code",
        processing_status="running",
    )
    revert = SimpleNamespace(id=10, stage_code="revert_pr", status="completed")
    notification = SimpleNamespace(
        id=11, stage_code="problem_pr_notification", status="completed"
    )
    merge = SimpleNamespace(
        id=12, stage_code="pr_merge", status="waiting" if existing else "pending", started_at=None
    )
    revert_history = SimpleNamespace(
        result_data={
            "pr_number": 2,
            "pr_url": "https://github.com/repo/test/pull/2",
            "original_pr_url": "https://github.com/repo/test/pull/1",
        }
    )
    rows = []
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[failure, revert_history]),
        scalars=AsyncMock(side_effect=[[revert, notification, merge], [existing] if existing else []]),
        get=AsyncMock(
            side_effect=[issue, SimpleNamespace(source_branch="main"), source]
        ),
        add=rows.append,
        rollback=AsyncMock(),
        commit=AsyncMock(),
    )
    tracking = SimpleNamespace(
        db=db, _complete_node=AsyncMock(), _commit_node_completion=AsyncMock()
    )
    prs = {
        1: make_pr(1, True, merged_at="2026-11-19T01:00:00Z"),
        2: make_pr(2, merged=revert_merged),
        23: repair,
    }
    comments = [make_comment(source.name)] if repair else []
    client = SimpleNamespace(
        pull_request=AsyncMock(side_effect=lambda number, **kwargs: prs[number]),
        issue=AsyncMock(return_value={"state": "open"}),
        issue_comments=AsyncMock(return_value=comments),
    )
    labels = module.labels_for_cases([(1, source)])
    refs = module.fix_references(comments, labels, issue.repository).get(1, [])
    return tracking, client, refs, labels, rows, failure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["fixed", "waiting", "deadline", "late_comment", "manual_revert", "gh_error"]
)
async def test_code_path_before_and_after_deadline(monkeypatch, scenario):
    point = datetime(2026, 11, 19, 3, 30)
    monkeypatch.setattr(module, "now", lambda: point)
    monkeypatch.setattr(
        module,
        "failure_notification_recipients",
        AsyncMock(return_value=["watcher", "reviewer"]),
    )
    repair = make_pr(23, merged=scenario == "fixed", merged_at="2026-11-19T03:22:00Z")
    tracking, client, refs, labels, rows, failure = make_flow(
        repair, revert_merged=scenario == "manual_revert"
    )
    deadline = datetime(2026, 11, 19, 3, 40) if scenario == "waiting" else point
    if scenario == "late_comment":
        refs = []
        repair.update(merged=True, state="closed")
    if scenario == "gh_error":
        client.issue_comments.side_effect = CollectionSourceError("read failed")
        with pytest.raises(CollectionSourceError):
            await module.advance_failure(tracking, client, 2, 1, refs, labels, deadline)
        assert not rows
        return
    await module.advance_failure(tracking, client, 2, 1, refs, labels, deadline)
    if scenario == "waiting":
        tracking._complete_node.assert_not_awaited()
        assert rows[0].result_data["repair"]["pr_number"] == 23
        return
    notices = [row.request_data for row in rows if row.request_data.get("purpose")]
    if scenario == "deadline":
        tracking._complete_node.assert_not_awaited()
        assert failure.processing_status == "unresolved"
        execution = rows[0]
        assert execution.status == "waiting"
        assert execution.result_data["deadline_notified"] is True
        assert {notice["github_name"] for notice in notices} == {
            "developer",
            "reviewer",
            "watcher",
        }
        assert len(notices) == 3
        for name, action in (
            ("watcher", "请跟踪回退 PR 合入进展。"),
            ("reviewer", "请处理回退 PR 合入。"),
            ("developer", "请知悉，本次失败已进入回退处理。"),
        ):
            assert action in next(notice["content"] for notice in notices if notice["github_name"] == name)

        tracking, client, refs, labels, rows, _ = make_flow(repair, existing=execution)
        await module.advance_failure(tracking, client, 2, 1, refs, labels, deadline)
        assert not rows
    else:
        assert tracking._complete_node.await_args.args[3] is True
        expected = "回退 PR 已合入" if scenario == "manual_revert" else "修复 PR 已合入"
        assert tracking._complete_node.await_args.args[5] == expected
        assert not notices
