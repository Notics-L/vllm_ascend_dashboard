"""Own the Daily mainline checkpoints used to recommend a source revision."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from infrastructure.clients.daily_pipeline_collection import (
    PAGE_SIZE,
    CollectionSourceError,
    DailyCollectionClient,
)
from infrastructure.core.security import decrypt_api_key
from infrastructure.persistence.daily_pipeline_settings import read_settings, set_setting
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE
from infrastructure.persistence.models import DailyPipelineCollectionNode as CollectionNode
from infrastructure.persistence.models import DailyPipelineConfig as Config
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as ProcessNode
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution
from infrastructure.persistence.models import DailyPipelineTriggerHistory as History

logger = logging.getLogger(__name__)
BASELINE_KEY = "last_daily_trigger_commit_sha"
PR_KEY_PREFIX = "last_daily_good_commit_sha_by_pr_"
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_LOOKUP_TIMEOUT = 30
_PR_LOOKUP_BATCH_SIZE = 6
_PR_METADATA_TIMEOUT = 5
_MAINLINE_SOURCE = "daily_branch_commits"


@dataclass(frozen=True)
class MainlinePoint:
    commit_sha: str
    committed_at: str
    mainline_position: int
    pr_number: int | None = None


@dataclass(frozen=True)
class SourceRecommendation:
    commit_sha: str
    pr_number: int | None
    branch: str
    trigger_history_id: int
    problem_pr_number: int | None
    repository: str | None = None
    committed_at: str | None = None


def _utc(value: str | datetime) -> datetime:
    point = datetime.fromisoformat(value) if isinstance(value, str) else value
    return point.replace(tzinfo=UTC) if point.tzinfo is None else point.astimezone(UTC)


def _config_value(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _has_sha(value: dict[str, Any]) -> bool:
    sha = value.get("commit_sha")
    return value.get("sha_source") == _MAINLINE_SOURCE and isinstance(sha, str) and _SHA.fullmatch(sha) is not None


async def _lock_baseline(db: AsyncSession):
    stmt = insert(Config).values(config_key=BASELINE_KEY, config_value=None)
    await db.execute(stmt.on_duplicate_key_update(config_key=stmt.inserted.config_key))
    return await db.scalar(
        select(Config).where(Config.config_key == BASELINE_KEY)
        .with_for_update().execution_options(populate_existing=True)
    )


def _round_value(history) -> dict[str, Any]:
    return {
        "trigger_history_id": history.id,
        "triggered_at": _utc(history.triggered_at).isoformat(),
        "repository": history.repository,
        "branch": history.source_branch,
        "commit_sha": None,
        "committed_at": None,
        "pr_number": None,
    }


async def begin_recommendation_round(db: AsyncSession, history) -> None:
    """Initialize the recommendation round for a committed Daily trigger."""
    if not history.run_id:
        return
    baseline = await _lock_baseline(db)
    previous = _config_value(baseline.config_value)
    value = _round_value(history)
    previous_time = str(previous.get("triggered_at") or "")
    previous_id = previous.get("trigger_history_id")
    if previous_id == history.id or (
        (previous_time, previous_id if type(previous_id) is int else 0)
        >= (value["triggered_at"], history.id)
    ):
        return
    await db.execute(
        delete(Config).where(Config.config_key.startswith(PR_KEY_PREFIX, autoescape=True))
    )
    baseline.config_value = json.dumps(value)
    await db.flush()


async def _recommendation_history(db: AsyncSession, baseline: dict[str, Any]):
    history_id = baseline.get("trigger_history_id")
    if type(history_id) is not int:
        return None
    return await db.scalar(select(History).where(
        History.id == history_id,
        History.run_id.is_not(None),
        History.result == "触发成功",
    ))


async def _checkpoints(db: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = await db.execute(
        select(Config.config_key, Config.config_value).where(Config.config_value.is_not(None), or_(
            Config.config_key == BASELINE_KEY,
            Config.config_key.startswith(PR_KEY_PREFIX, autoescape=True),
        ))
    )
    return {key: _config_value(raw) for key, raw in rows}


async def _active_problem_prs(db: AsyncSession, run_id: int, repository: str) -> set[int]:
    replacement = aliased(Failure)
    replacement_node = aliased(CollectionNode)
    superseded_by_other_run = (
        select(replacement.id)
        .join(replacement_node, replacement_node.id == replacement.node_id)
        .where(replacement.id == Failure.replaced_by_id, replacement_node.run_id != run_id)
        .exists()
    )
    issue_closed = select(Issue.id).where(Issue.id == Failure.issue_id, Issue.state == "closed").exists()
    rows = await db.execute(
        select(Failure.id, Execution.result_data)
        .select_from(Failure)
        .join(CollectionNode, CollectionNode.id == Failure.node_id)
        .join(ProcessNode, ProcessNode.failure_id == func.coalesce(Failure.shared_flow_owner_id, Failure.id))
        .join(Execution, Execution.node_id == ProcessNode.id)
        .where(
            CollectionNode.run_id == run_id,
            Failure.problem_category == "code",
            or_(
                Failure.processing_status.in_(ACTIVE),
                # A different Run collecting the same case can supersede ordinary tracking.
                and_(
                    Failure.end_reason == "superseded",
                    Failure.processing_status == "ended",
                    superseded_by_other_run,
                    ~issue_closed,
                ),
            ),
            ProcessNode.stage_code == "locating",
            ProcessNode.status == "completed",
            Execution.status == "completed",
        )
        .order_by(Failure.id, Execution.attempt_no.desc())
    )
    latest = {}
    for failure_id, result in rows:
        latest.setdefault(failure_id, result if isinstance(result, dict) else {})
    return {
        result["pr_number"] for result in latest.values()
        if str(result.get("pr_repository", "")).casefold() == repository.casefold()
        and type(result.get("pr_number")) is int and result["pr_number"] > 0
    }


def _commit_sha(value: Any) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise CollectionSourceError("GitHub 分支提交缺少有效 SHA")
    return value.lower()


def _first_parent(commit: dict[str, Any]) -> str | None:
    parents = commit.get("parents")
    if not isinstance(parents, list):
        raise CollectionSourceError("GitHub 分支提交缺少父提交信息")
    return _commit_sha(parents[0]["sha"]) if parents else None


async def _mainline_batches(
    client: DailyCollectionClient, start_sha: str, before: datetime,
) -> AsyncIterator[list[MainlinePoint]]:
    sha: str | None = _commit_sha(start_sha)
    by_sha: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    position = 0

    while sha is not None:
        batch = []
        while sha is not None and len(batch) < _PR_LOOKUP_BATCH_SIZE:
            if sha in seen:
                raise CollectionSourceError("GitHub 分支提交链出现重复 SHA")
            if sha not in by_sha:
                commits = await client.json(f"{client.prefix}/commits", {"sha": sha, "per_page": PAGE_SIZE})
                if not isinstance(commits, list):
                    raise CollectionSourceError("GitHub 返回了无效的分支提交列表")
                by_sha = {_commit_sha(commit["sha"]): commit for commit in commits}
                if sha not in by_sha:
                    commit = await client.json(f"{client.prefix}/commits/{sha}", {"per_page": 1})
                    if _commit_sha(commit["sha"]) != sha:
                        raise CollectionSourceError("GitHub 返回了不匹配的分支提交")
                    by_sha[sha] = commit
            commit = by_sha[sha]
            committed_at = _utc(commit["commit"]["committer"]["date"])
            if committed_at < before:
                batch.append(MainlinePoint(sha, committed_at.isoformat(), position))
                position += 1
            seen.add(sha)
            sha = _first_parent(commit)
        if batch:
            yield batch


def _merged_on_branch(pull: dict[str, Any], branch: str, before: datetime) -> datetime | None:
    merged_at = pull.get("merged_at")
    if not merged_at or (pull.get("base") or {}).get("ref") != branch:
        return None
    point = _utc(merged_at)
    return point if point < before else None


async def _commit_pull_requests(client: DailyCollectionClient, sha: str) -> list[dict[str, Any]]:
    try:
        async with asyncio.timeout(_PR_METADATA_TIMEOUT):
            return await client.pages(f"{client.prefix}/commits/{sha}/pulls")
    except (CollectionSourceError, TimeoutError):
        return []


async def _mainline_points(
    client: DailyCollectionClient, branch: str, before: datetime, start_sha: str,
) -> AsyncIterator[tuple[MainlinePoint, set[int]]]:
    seen_prs: set[int] = set()
    async for batch in _mainline_batches(client, start_sha, before):
        results = await asyncio.gather(*(_commit_pull_requests(client, point.commit_sha) for point in batch))
        for point, pulls in zip(batch, results, strict=True):
            merged = {}
            for pull in pulls:
                if not isinstance(pull, dict) or not isinstance(pull.get("base"), dict):
                    continue
                number = pull.get("number")
                if type(number) is not int or number <= 0:
                    continue
                try:
                    merged_at = _merged_on_branch(pull, branch, before)
                except (TypeError, ValueError):
                    continue
                if merged_at is not None:
                    merged[number] = merged_at
            new_prs = merged.keys() - seen_prs
            if new_prs:
                number = max(new_prs, key=lambda number: (merged[number], number))
                yield MainlinePoint(
                    point.commit_sha, point.committed_at, point.mainline_position,
                    number,
                ), new_prs
                seen_prs.update(new_prs)
            elif not merged:
                # Unassociated commits are valid points. Repeated PR associations are skipped.
                yield point, set()


async def refresh_source_recommendation(db: AsyncSession) -> bool:
    """Update the current Daily round. Return whether its recommendations are ready."""
    checkpoints = await _checkpoints(db)
    baseline = checkpoints.get(BASELINE_KEY, {})
    history = await _recommendation_history(db, baseline)
    if history is None:
        return True
    active = await _active_problem_prs(db, history.run_id, history.repository)
    settings = await read_settings(db, "github_token")
    await db.commit()

    baseline_ready = _has_sha(baseline)
    needed = []
    for number in sorted(active):
        saved = checkpoints.get(f"{PR_KEY_PREFIX}{number}", {})
        if not _has_sha(saved) and not saved.get("ineligible"):
            needed.append(number)
    baseline_point = None
    candidates = {}
    ineligible = set()
    token = decrypt_api_key(settings.get("github_token") or "")
    if token and (needed or not baseline_ready):
        try:
            async with asyncio.timeout(_LOOKUP_TIMEOUT), DailyCollectionClient(history.repository, token) as client:
                cutoff = _utc(history.triggered_at)
                branch = history.source_branch.removeprefix("refs/heads/")
                pending = set()
                for number in needed:
                    culprit = await client.pull_request(number, missing_ok=True)
                    if culprit is not None and _merged_on_branch(culprit, branch, cutoff) is None:
                        ineligible.add(number)
                    elif culprit is not None:
                        pending.add(number)
                awaiting_predecessor: set[int] = set()
                if not baseline_ready or pending:
                    start_sha = baseline.get("commit_sha")
                    if not baseline_ready:
                        # The recorded run anchors this round to the dispatched branch revision.
                        run = await client.run(history.run_id)
                        start_sha = _commit_sha(run["head_sha"])
                    async for point, prs in _mainline_points(client, branch, cutoff, start_sha):
                        if baseline_point is None:
                            baseline_point = point
                        for number in awaiting_predecessor:
                            candidates[number] = point
                        awaiting_predecessor = pending.intersection(prs)
                        pending.difference_update(awaiting_predecessor)
                        if not pending and not awaiting_predecessor:
                            break
        except (CollectionSourceError, TimeoutError, KeyError, TypeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, CollectionSourceError) else type(exc).__name__
            logger.warning(
                "Daily source recommendation lookup incomplete history_id=%s repo=%s branch=%s reason=%s",
                history.id, history.repository, history.source_branch, reason,
            )

    baseline_row = await _lock_baseline(db)
    baseline = _config_value(baseline_row.config_value)
    if baseline.get("trigger_history_id") != history.id:
        await db.rollback()
        return False
    if baseline_point is not None and (
        not _has_sha(baseline) or (
            baseline.get("commit_sha") == baseline_point.commit_sha
            and baseline.get("pr_number") is None
            and baseline_point.pr_number is not None
        )
    ):
        baseline_row.config_value = json.dumps({
            **_round_value(history), **asdict(baseline_point), "sha_source": _MAINLINE_SOURCE,
        })
    active = await _active_problem_prs(db, history.run_id, history.repository)
    checkpoints = await _checkpoints(db)
    previous_prs = {
        int(key.removeprefix(PR_KEY_PREFIX)): value
        for key, value in checkpoints.items()
        if key.startswith(PR_KEY_PREFIX) and key.removeprefix(PR_KEY_PREFIX).isdigit()
        and value.get("trigger_history_id") == history.id
    }
    complete = _has_sha(_config_value(baseline_row.config_value))
    for number in sorted(active | previous_prs.keys()):
        previous = previous_prs.get(number, {})
        point = candidates.get(number)
        if not previous and number not in active:
            continue
        excluded = number in ineligible or previous.get("ineligible", False)
        value = {**(previous or _round_value(history)), "outdate": excluded or number not in active}
        if excluded:
            value["ineligible"] = True
        if point is not None and number in active and not _has_sha(previous):
            value.pop("merged_at", None)
            value.update(asdict(point), sha_source=_MAINLINE_SOURCE)
        if number in active and not excluded and not _has_sha(value):
            complete = False
        if value != previous:
            await set_setting(db, f"{PR_KEY_PREFIX}{number}", json.dumps(value))
    await db.commit()
    return complete


async def get_source_recommendation(
    db: AsyncSession, branch: str, *, repository: str | None = None,
) -> SourceRecommendation | None:
    """Read the Daily recommendation for a source branch and optional repository."""
    checkpoints = await _checkpoints(db)
    baseline = checkpoints.get(BASELINE_KEY, {})
    saved_branch = baseline.get("branch")
    if not isinstance(saved_branch, str) or saved_branch.removeprefix("refs/heads/") != branch.removeprefix("refs/heads/"):
        return None
    history = await _recommendation_history(db, baseline)
    if history is None:
        return None
    history_id = history.id
    if repository is not None and history.repository.casefold() != repository.casefold():
        return None
    active = await _active_problem_prs(db, history.run_id, history.repository)
    valid = []
    for number in active:
        item = checkpoints.get(f"{PR_KEY_PREFIX}{number}", {})
        if item.get("trigger_history_id") == history_id and item.get("ineligible"):
            continue
        if item.get("trigger_history_id") != history_id or item.get("outdate") or not _has_sha(item):
            return None
        valid.append((item.get("mainline_position", 0), number, item))
    problem_pr = None
    point = baseline
    if valid:
        _, problem_pr, point = max(valid)
    if not _has_sha(point):
        return None
    return SourceRecommendation(
        commit_sha=point["commit_sha"],
        pr_number=point.get("pr_number"),
        committed_at=point.get("committed_at"),
        branch=point["branch"],
        trigger_history_id=history_id,
        problem_pr_number=problem_pr,
        repository=history.repository,
    )
