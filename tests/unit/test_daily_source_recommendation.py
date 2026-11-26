import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from daily_pipeline import dispatch
from daily_pipeline import recommendation_events
from daily_pipeline import source_recommendation as daily
from infrastructure.persistence.models import (
    DailyPipelineCollectionNode,
    DailyPipelineConfig,
    DailyPipelineFailure,
    DailyPipelineIssue,
    DailyPipelineProcessNode,
    DailyPipelineProcessNodeExecution,
)


def _commit(sha, *parents, time="07:00:00"):
    return {
        "sha": sha,
        "parents": [{"sha": parent} for parent in parents],
        "commit": {"committer": {"date": f"2026-11-26T{time}Z"}},
    }


def _pull(number, merged_at, branch="release/test"):
    return {"number": number, "merged_at": merged_at, "base": {"ref": branch}}


def _github_fixture():
    prefix = "/repos/daily-team/benchmarks"
    head, culprit, repeated, previous, side = (digit * 40 for digit in "abcde")
    commits = [
        _commit(side),
        _commit(repeated, previous, time="07:20:00"),
        _commit(head, culprit, side, time="08:00:00"),
        _commit(previous, time="06:55:00"),
        _commit(culprit, repeated, time="07:30:00"),
    ]
    associated = {
        head: [_pull(426, "2026-11-26T08:00:00Z")],
        culprit: [_pull(425, "2026-11-26T07:00:00Z")],
        repeated: [_pull(425, "2026-11-26T07:00:00Z")],
        previous: [_pull(424, "2026-11-26T07:00:00Z"), _pull(423, "2026-11-26T06:00:00Z", "other")],
    }

    async def json(path, params=None):
        assert path == f"{prefix}/commits"
        assert params["sha"] in (head, culprit)
        return commits

    async def pages(path):
        sha = path.split("/")[-2]
        assert path == f"{prefix}/commits/{sha}/pulls"
        return associated[sha]

    return SimpleNamespace(
        prefix=prefix,
        json=AsyncMock(side_effect=json),
        pages=AsyncMock(side_effect=pages),
        pull_request=AsyncMock(return_value=associated[culprit][0]),
        run=AsyncMock(return_value={"head_sha": head}),
    )


@pytest.mark.asyncio
async def test_merge_points_follow_first_parent_and_exclude_commits_after_cutoff():
    client = _github_fixture()
    cutoff = datetime(2026, 11, 26, 8, tzinfo=UTC)
    points = [point async for point in daily._mainline_points(client, "release/test", cutoff, "a" * 40)]

    assert points == [
        (daily.MainlinePoint("b" * 40, "2026-11-26T07:30:00+00:00", 0, 425), {425}),
        (daily.MainlinePoint("d" * 40, "2026-11-26T06:55:00+00:00", 2, 424), {424}),
    ]
    queried = [call.args[0].split("/")[-2] for call in client.pages.await_args_list]
    assert queried == [digit * 40 for digit in "bcd"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_pr", [None, "baseline", "candidate", "candidate_error"])
async def test_refreshed_baseline_and_culprit_predecessor_come_from_mainline(monkeypatch, missing_pr):
    cutoff = datetime(2026, 11, 26, 8, tzinfo=UTC)
    history = SimpleNamespace(
        id=5, run_id=23, repository="daily-team/benchmarks", source_branch="release/test",
        triggered_at=cutoff,
    )
    client = _github_fixture()
    read_pulls = client.pages.side_effect

    async def lookup_pulls(path):
        sha = path.split("/")[-2]
        if missing_pr == "baseline" and sha == "b" * 40:
            return []
        if sha == "d" * 40:
            if missing_pr == "candidate":
                return []
            if missing_pr == "candidate_error":
                raise daily.CollectionSourceError("PR lookup unavailable")
        return await read_pulls(path)

    client.pages.side_effect = lookup_pulls
    baseline = SimpleNamespace(config_value='{"trigger_history_id": 5, "branch": "release/test"}')
    db = SimpleNamespace(commit=AsyncMock())
    saved_candidates = {}

    async def checkpoints(_db):
        return {daily.BASELINE_KEY: daily._config_value(baseline.config_value), **saved_candidates}

    async def save_candidate(_db, key, raw):
        saved_candidates[key] = daily._config_value(raw)
    monkeypatch.setattr(daily, "_recommendation_history", AsyncMock(return_value=history))
    monkeypatch.setattr(daily, "_checkpoints", checkpoints)
    active = set() if missing_pr == "baseline" else {425}
    monkeypatch.setattr(daily, "_active_problem_prs", AsyncMock(return_value=active))
    monkeypatch.setattr(daily, "_lock_baseline", AsyncMock(return_value=baseline))
    monkeypatch.setattr(daily, "read_settings", AsyncMock(return_value={"github_token": "dummy"}))
    monkeypatch.setattr(daily, "decrypt_api_key", lambda _: "dummy")
    monkeypatch.setattr(daily, "set_setting", save_candidate)

    class DailyGitHub:
        def __init__(self, repository, token):
            assert repository == history.repository

        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            pass

    monkeypatch.setattr(daily, "DailyCollectionClient", DailyGitHub)
    await daily.refresh_source_recommendation(db)

    client.run.assert_awaited_once_with(history.run_id)
    baseline_value = daily._config_value(baseline.config_value)
    assert baseline_value["pr_number"] == (None if missing_pr == "baseline" else 425)
    assert baseline_value["commit_sha"] == "b" * 40
    assert baseline_value["committed_at"] == "2026-11-26T07:30:00+00:00"
    assert baseline_value["sha_source"] == daily._MAINLINE_SOURCE
    assert "merged_at" not in baseline_value

    result = await daily.get_source_recommendation(db, history.source_branch, repository=history.repository)
    if missing_pr == "baseline":
        assert result.commit_sha == "b" * 40
        assert result.pr_number is None
        assert result.committed_at == baseline_value["committed_at"]
        return

    candidate = saved_candidates[f"{daily.PR_KEY_PREFIX}425"]
    assert candidate["pr_number"] == (424 if missing_pr is None else None)
    assert candidate["commit_sha"] == "d" * 40
    assert candidate["committed_at"] == "2026-11-26T06:55:00+00:00"
    assert candidate["outdate"] is False
    assert candidate["mainline_position"] == 2
    assert result.commit_sha == candidate["commit_sha"]
    assert result.committed_at == candidate["committed_at"]
    assert result.pr_number == candidate["pr_number"]
    assert result.problem_pr_number == 425


@pytest.mark.asyncio
async def test_daily_recommendation_uses_its_own_repository(monkeypatch):
    sha = "a" * 40
    branch = "release/test"
    repository = "daily-team/benchmarks"
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        id=5, run_id=21, repository=repository,
    )))
    monkeypatch.setattr(daily, "_checkpoints", AsyncMock(return_value={
        daily.BASELINE_KEY: {
            "trigger_history_id": 5, "branch": branch, "commit_sha": sha,
            "pr_number": 425, "committed_at": "2026-11-23T06:20:45+00:00", "sha_source": daily._MAINLINE_SOURCE,
        },
    }))
    active = AsyncMock(return_value=set())
    monkeypatch.setattr(daily, "_active_problem_prs", active)

    result = await daily.get_source_recommendation(db, branch)
    assert result.commit_sha == sha
    assert result.repository == repository
    active.assert_awaited_once_with(db, 21, repository)
    assert await daily.get_source_recommendation(db, branch, repository="vllm-project/vllm-ascend") is None


@pytest.mark.asyncio
async def test_selects_earliest_mainline_candidate_without_pr_metadata(monkeypatch):
    branch = "main"
    date = "2026-11-23T06:20:45+00:00"
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        id=5, run_id=21, repository="daily-team/benchmarks",
    )))
    round_values = {"trigger_history_id": 5, "branch": branch, "sha_source": daily._MAINLINE_SOURCE}
    monkeypatch.setattr(daily, "_checkpoints", AsyncMock(return_value={
        daily.BASELINE_KEY: round_values,
        f"{daily.PR_KEY_PREFIX}425": {
            **round_values, "commit_sha": "a" * 40, "pr_number": 100,
            "committed_at": date, "mainline_position": 1,
        },
        f"{daily.PR_KEY_PREFIX}426": {
            **round_values, "commit_sha": "b" * 40, "pr_number": None,
            "committed_at": date, "mainline_position": 2,
        },
    }))
    monkeypatch.setattr(daily, "_active_problem_prs", AsyncMock(return_value={425, 426}))

    result = await daily.get_source_recommendation(db, branch)

    assert result.commit_sha == "b" * 40
    assert result.problem_pr_number == 426
    assert result.pr_number is None


@pytest.mark.asyncio
async def test_pending_recommendation_round_does_not_use_previous_sha(monkeypatch):
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(
        id=6, run_id=22, repository="example/project",
    )))
    monkeypatch.setattr(daily, "_checkpoints", AsyncMock(return_value={
        daily.BASELINE_KEY: {"trigger_history_id": 6, "branch": "main", "commit_sha": None},
    }))
    monkeypatch.setattr(daily, "_active_problem_prs", AsyncMock(return_value=set()))

    assert await daily.get_source_recommendation(db, "main") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("options", "participates", "initialization_fails"), [
    ({}, True, False),
    ({}, True, True),
    ({"event": "手动触发", "include_in_recommendation": False}, False, False),
    ({"event": "手动触发", "include_in_recommendation": True}, True, False),
    ({"event": "定时触发", "include_in_recommendation": False}, True, False),
])
async def test_daily_dispatch_commits_before_initializing_recommendation(
    monkeypatch, options, participates, initialization_fails,
):
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), info={})
    recommendation_db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    history = SimpleNamespace(id=7, triggered_at=datetime(2026, 11, 26, 8))
    save_history = AsyncMock(return_value=history)
    save_collection = AsyncMock()
    register_collection = AsyncMock()
    initialize = AsyncMock(side_effect=RuntimeError("recommendation write failed") if initialization_fails else None)

    @asynccontextmanager
    async def recommendation_transaction():
        db.commit.assert_awaited_once()
        save_history.assert_awaited_once()
        save_collection.assert_awaited_once_with(1234, dispatch.WORKFLOW, commit=False)
        register_collection.assert_awaited_once_with(1234)
        try:
            yield recommendation_db
        except RuntimeError:
            await recommendation_db.rollback()
            raise
        else:
            await recommendation_db.commit()

    settings = {"repository": "example/project", "source_branch": "main", "github_token": "test-token"}
    response = {"id": "7", "result": "触发成功", "runId": 1234}
    monkeypatch.setattr(dispatch, "SessionLocal", SimpleNamespace(begin=recommendation_transaction))
    monkeypatch.setattr(dispatch, "read_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(dispatch, "decrypt_api_key", lambda token: token)
    monkeypatch.setattr(dispatch, "DailyCaseRepository", Mock(return_value=SimpleNamespace(
        list_cases=AsyncMock(return_value=[]),
    )))
    monkeypatch.setattr(dispatch, "active_daily_failure_times", AsyncMock(return_value={}))
    monkeypatch.setattr(dispatch, "dispatch_daily_workflow", AsyncMock(return_value=1234))
    monkeypatch.setattr(dispatch, "DailyTriggerHistoryRepository", Mock(return_value=SimpleNamespace(
        add_history=save_history,
    )))
    monkeypatch.setattr(dispatch, "DailyCollectionRepository", Mock(return_value=SimpleNamespace(
        ensure_root=save_collection,
    )))
    monkeypatch.setattr(dispatch, "start_collection", register_collection)
    monkeypatch.setattr(dispatch, "begin_recommendation_round", initialize)
    monkeypatch.setattr(dispatch, "history_detail", Mock(return_value=response))

    assert await dispatch.trigger_daily_pipeline(db, **options) == response
    assert save_history.await_args.kwargs["run_id"] == 1234
    assert save_history.await_args.kwargs["commit"] is False
    assert "include_in_recommendation" not in save_history.await_args.kwargs
    assert bool(db.info) is participates
    register_collection.assert_awaited_once_with(1234)
    if participates:
        initialize.assert_awaited_once_with(recommendation_db, history)
    else:
        initialize.assert_not_awaited()
    db.rollback.assert_not_awaited()
    assert recommendation_db.commit.await_count == int(participates and not initialization_fails)
    assert recommendation_db.rollback.await_count == int(initialization_fails)


@pytest.mark.asyncio
async def test_new_round_deletes_all_previous_candidates_once(monkeypatch):
    engine = create_engine("sqlite://")
    DailyPipelineConfig.__table__.create(engine)
    point = datetime(2026, 11, 26, 8, tzinfo=UTC)
    old_round = SimpleNamespace(
        id=5, run_id=23, repository="example/project", source_branch="main", triggered_at=point,
    )
    new_round = SimpleNamespace(
        id=6, run_id=24, repository="example/project", source_branch="main", triggered_at=point,
    )
    active, expired, cleared = (f"{daily.PR_KEY_PREFIX}{number}" for number in (100, 101, 102))
    unrelated = "lastXdailyXgoodXcommitXshaXbyXprX103"
    with Session(engine) as session:
        baseline = DailyPipelineConfig(config_key=daily.BASELINE_KEY, config_value=json.dumps(daily._round_value(old_round)))
        session.add_all([
            baseline,
            DailyPipelineConfig(config_key=active, config_value='{"outdate": false}'),
            DailyPipelineConfig(config_key=expired, config_value='{"outdate": true}'),
            DailyPipelineConfig(config_key=cleared),
            DailyPipelineConfig(config_key=unrelated, config_value="keep"),
            DailyPipelineConfig(config_key="repository", config_value="example/project"),
        ])
        session.commit()
        db = SimpleNamespace(execute=AsyncMock(side_effect=session.execute), flush=AsyncMock(side_effect=session.flush))
        monkeypatch.setattr(daily, "_lock_baseline", AsyncMock(return_value=baseline))

        await daily.begin_recommendation_round(db, new_round)
        assert set(session.scalars(select(DailyPipelineConfig.config_key))) == {
            daily.BASELINE_KEY, unrelated, "repository",
        }
        assert json.loads(baseline.config_value)["trigger_history_id"] == 6

        session.add(DailyPipelineConfig(config_key=active, config_value='{"trigger_history_id": 6}'))
        session.flush()
        await daily.begin_recommendation_round(db, new_round)
        await daily.begin_recommendation_round(db, old_round)
        db.execute.assert_awaited_once()
        assert active in set(session.scalars(select(DailyPipelineConfig.config_key)))


@pytest.mark.asyncio
async def test_only_committed_daily_changes_trigger_recommendation(monkeypatch):
    updated = asyncio.Queue()
    recommendation_db = SimpleNamespace(get=AsyncMock(), commit=AsyncMock())
    initialize = AsyncMock()

    class Sessions:
        class_ = AsyncSession

        @asynccontextmanager
        async def __call__(self):
            yield recommendation_db

    async def refresh(db):
        assert db is recommendation_db
        updated.put_nowait(True)
        return True

    monkeypatch.setattr(recommendation_events, "SessionLocal", Sessions())
    monkeypatch.setattr(recommendation_events, "refresh_source_recommendation", refresh)
    monkeypatch.setattr(recommendation_events, "begin_recommendation_round", initialize)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for model in (daily.History, DailyPipelineFailure, DailyPipelineProcessNode):
            model.__table__.create(connection)
    point = datetime(2026, 11, 26, 8)
    with Session(engine, expire_on_commit=False) as session:
        failure = DailyPipelineFailure(
            id=1, node_id=1, problem_category="code", processing_status="running", discovered_at=point,
        )
        node = DailyPipelineProcessNode(id=1, failure_id=1, stage_code="locating", status="running", updated_at=point)
        session.add_all([failure, node])
        session.commit()

        async with recommendation_events.watch_daily_recommendations():
            session.add(daily.History(
                id=2, run_id=3, result="触发成功", event="手动触发", repository="example/project",
                source_branch="main", workflow="test.yaml", triggered_at=point,
            ))
            session.commit()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert updated.empty()

            history = daily.History(
                id=1, run_id=2, result="触发成功", event="手动触发", repository="example/project",
                source_branch="main", workflow="test.yaml", triggered_at=point,
            )
            session.add(history)
            recommendation_db.get.return_value = history
            recommendation_events.note_recommendation_round(session, history)
            session.commit()
            await asyncio.wait_for(updated.get(), 1)
            initialize.assert_awaited_once_with(recommendation_db, history)

            node.status = "completed"
            session.flush()
            session.rollback()
            session.commit()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert updated.empty()

            node.status = "completed"
            session.commit()
            await asyncio.wait_for(updated.get(), 1)

            failure.processing_status = "ended"
            session.commit()
            await asyncio.wait_for(updated.get(), 1)


@pytest.mark.asyncio
async def test_opt_out_run_preserves_recommendation_when_the_same_case_fails_again():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.connection.driver_connection.create_collation("utf8mb4_bin", lambda a, b: (a > b) - (a < b))
        for model in (
            daily.History, DailyPipelineConfig, DailyPipelineCollectionNode, DailyPipelineIssue,
            DailyPipelineFailure, DailyPipelineProcessNode, DailyPipelineProcessNodeExecution,
        ):
            model.__table__.create(connection)

    repository = "example/project"
    point = datetime(2026, 11, 26, 8)
    sha = "b" * 40
    with Session(engine) as session:
        issue = DailyPipelineIssue(id=1, run_id=100, repository=repository, number=12, title="Daily", state="open")
        session.add_all([
            daily.History(
                id=identity, run_id=run_id, event="手动触发", result="触发成功", repository=repository,
                source_branch="main", workflow="test.yaml", triggered_at=point.replace(minute=identity),
            )
            for identity, run_id in ((1, 100), (2, 101))
        ])
        session.add_all([
            DailyPipelineCollectionNode(id=identity, run_id=run_id, node_type="test_job", name="case", result="failure")
            for identity, run_id in ((1, 100), (2, 101))
        ])
        session.add_all([
            issue,
            DailyPipelineFailure(
                id=1, issue_id=1, node_id=1, problem_category="code", processing_status="ended",
                end_reason="superseded", replaced_by_id=2, discovered_at=point,
            ),
            DailyPipelineFailure(id=2, node_id=2, processing_status="pending", discovered_at=point),
            DailyPipelineProcessNode(id=1, failure_id=1, stage_code="locating", status="completed", updated_at=point),
            DailyPipelineProcessNodeExecution(
                id=1, node_id=1, attempt_no=1, status="completed", started_at=point,
                result_data={"pr_repository": repository, "pr_number": 425},
            ),
            DailyPipelineConfig(config_key=daily.BASELINE_KEY, config_value=json.dumps({
                "trigger_history_id": 1, "branch": "main", "commit_sha": "a" * 40,
                "sha_source": daily._MAINLINE_SOURCE,
            })),
            DailyPipelineConfig(config_key=f"{daily.PR_KEY_PREFIX}425", config_value=json.dumps({
                "trigger_history_id": 1, "branch": "main", "commit_sha": sha,
                "sha_source": daily._MAINLINE_SOURCE,
            })),
        ])
        session.commit()
        db = SimpleNamespace(execute=AsyncMock(side_effect=session.execute), scalar=AsyncMock(side_effect=session.scalar))

        recommendation = await daily.get_source_recommendation(db, "main", repository=repository)
        assert recommendation.commit_sha == sha
        assert recommendation.trigger_history_id == 1
        assert recommendation.problem_pr_number == 425

        issue.state = "closed"
        session.commit()
        recommendation = await daily.get_source_recommendation(db, "main", repository=repository)
        assert recommendation.commit_sha == "a" * 40
        assert recommendation.problem_pr_number is None
