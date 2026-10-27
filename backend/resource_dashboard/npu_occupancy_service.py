"""Normalization and aggregation for the read-through NPU occupancy API."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from infrastructure.github_run_metadata import build_run_metadata, workflow_file_from_ref
from resource_dashboard.npu_occupancy_client import NpuOccupancyClient
from resource_dashboard.npu_occupancy_config import (
    POOL_BY_NAME,
    POOLS,
    configured_pool_options,
    is_excluded_cluster,
    resolve_pool,
)

STEP_SECONDS = 120


@dataclass(frozen=True)
class OccupancyRecord:
    env_id: str
    organization: str | None
    repository: str | None
    pool: str
    project: str
    workflow: str
    workflow_run_id: int | None
    workflow_ref: str | None
    job_id: str | None
    job_name: str
    runner_id: str | None
    workflow_name: str | None
    workflow_file: str | None
    run_display_title: str | None
    run_number: int | None
    run_display_name: str
    pr_number: int | None
    event: str | None
    trigger_scope: str | None
    run_url: str | None
    github_url: str | None
    workflow_config_match: bool
    cards: int
    status: str
    created_at: datetime
    effective_end: datetime
    node_ip: str | None
    npu_list: str | None


def parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if re.search(r"[+-]\d{2}$", text):
        text += ":00"
    elif re.search(r"[+-]\d{4}$", text):
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(re.sub(r"\.(\d+)", "", text))
        except ValueError:
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def npu_cards(env: dict) -> int:
    total = 0
    for device in ((env.get("resource_summary") or {}).get("devices") or []):
        if device.get("res_type") == "npu":
            try:
                total += int(device.get("npu") or 0)
            except (TypeError, ValueError):
                continue
    return total


def project_identity(env: dict) -> tuple[str | None, str | None]:
    """Return a canonical GitHub owner/repository pair from upstream metadata.

    Pod history sometimes stores the repository in its legacy hyphenated form
    (for example ``sgl-project-sglang``).  Keep the conversion at the ingest
    boundary so grouping and generated GitHub links use the same identity.
    """
    comments = env.get("extend_env_comments") or {}
    organization = str(comments.get("organization") or "").strip() or None
    # ARC runner payloads use ``job_repository`` while other producers use
    # ``repository``.  They describe the same GitHub identity.
    repository = str(comments.get("repository") or comments.get("job_repository") or "").strip() or None

    if organization and repository:
        # Some upstreams put the full ``owner/repository`` value in
        # ``repository`` as well as supplying ``organization`` separately.
        # Prefer that complete identifier instead of producing invalid paths
        # such as ``Ascend/Ascend/pytorch``.
        normalized_repository = repository.strip("/")
        if "/" in normalized_repository:
            owner, repo = normalized_repository.split("/", 1)
            if owner and repo:
                return owner, repo
        # Some legacy payloads supply a canonical organization alongside a
        # hyphenated full project identifier (``sgl-project-sglang``).  The
        # matching organization makes this conversion unambiguous; do not
        # split arbitrary hyphenated repository names.
        legacy_match = re.fullmatch(r"(?P<owner>.+-project)-(?P<repo>.+)", normalized_repository)
        if legacy_match and legacy_match["owner"].lower() == organization.lower():
            return organization, legacy_match["repo"]
        return organization, repository

    candidate = repository or organization
    if candidate:
        normalized = candidate.strip("/")
        if "/" in normalized:
            owner, repo = normalized.split("/", 1)
            if owner and repo:
                return owner, repo
        match = re.fullmatch(r"(?P<owner>.+-project)-(?P<repo>.+)", normalized)
        if match:
            return match["owner"], match["repo"]

    workflow_ref = str(comments.get("workflow_ref") or "")
    match = re.match(r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/\.github/workflows/", workflow_ref)
    if match:
        return match["owner"], match["repo"]
    return organization, repository


def project_of(env: dict) -> str:
    organization, repository = project_identity(env)
    if organization and repository:
        return f"{organization}/{repository}"
    if repository:
        return repository
    groups = env.get("groups") or {}
    return sorted(groups)[0] if groups else "未知"


def workflow_of(env: dict) -> str:
    comments = env.get("extend_env_comments") or {}
    return str(comments.get("job_display_name") or env.get("name") or env.get("env_id") or "")


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", value.lower())


def normalize_records(envs: list[dict], snapshot_at: datetime) -> list[OccupancyRecord]:
    records: list[OccupancyRecord] = []
    seen: set[str] = set()
    for env in envs:
        env_id = str(env.get("env_id") or "")
        if not env_id or env_id in seen or env.get("status") == "rejected":
            continue
        cluster = str(env.get("cluster") or "")
        if is_excluded_cluster(cluster):
            continue
        pool_config = resolve_pool(cluster)
        pool_name = pool_config.name if pool_config else f"未配置:{cluster or '未知资源池'}"
        created_at = parse_timestamp(env.get("created_at"))
        if created_at is None:
            continue
        status = str(env.get("status") or "")
        expires_at = parse_timestamp(env.get("expires_at"))
        effective_end = snapshot_at if status in {"active", "provisioning"} else expires_at
        cards = npu_cards(env)
        if effective_end is None or created_at >= effective_end or cards <= 0:
            continue
        seen.add(env_id)
        npu_list = env.get("npu_list")
        if isinstance(npu_list, list):
            npu_list = ",".join(str(item) for item in npu_list)
        elif npu_list is not None:
            npu_list = str(npu_list)
        comments = env.get("extend_env_comments") or {}
        organization, repository = project_identity(env)
        workflow_run_id = _positive_int(comments.get("workflow_run_id"))
        workflow_ref = str(comments.get("workflow_ref") or "") or None
        workflow = workflow_of(env)
        metadata = build_run_metadata(
            organization=organization,
            repository=repository,
            workflow_run_id=workflow_run_id,
            workflow_ref=workflow_ref,
            job_display_name=workflow,
        )
        records.append(OccupancyRecord(
            env_id=env_id, organization=organization, repository=repository,
            pool=pool_name, project=project_of(env), workflow=workflow,
            job_id=str(comments.get("job_id") or "") or None,
            job_name=workflow,
            runner_id=str(comments.get("runner_id") or "") or None,
            cards=cards, status=status, created_at=created_at, effective_end=effective_end,
            node_ip=env.get("node_ip"), npu_list=npu_list,
            **metadata,
        ))
    return records


def enrich_records_with_runs(
    records: list[OccupancyRecord], results_by_run_id: dict[int, object], jobs_by_id: dict[int, object] | None = None,
    pr_titles_by_number: dict[int, str] | None = None,
    workflow_names_by_ref: dict[tuple[str, str, str], str] | None = None,
    workflow_names_by_run_id: dict[int, str] | None = None,
) -> list[OccupancyRecord]:
    """Attach CI Run and Job metadata after batched database lookups."""
    jobs_by_id = jobs_by_id or {}
    pr_titles_by_number = pr_titles_by_number or {}
    workflow_names_by_ref = workflow_names_by_ref or {}
    workflow_names_by_run_id = workflow_names_by_run_id or {}
    return [
        replace(
            record,
            job_name=getattr(jobs_by_id.get(_positive_int(record.job_id)), "job_name", None) or record.job_name,
            **build_run_metadata(
                organization=record.organization,
                repository=record.repository,
                workflow_run_id=record.workflow_run_id,
                workflow_ref=record.workflow_ref,
                job_display_name=record.workflow,
                ci_result=results_by_run_id.get(record.workflow_run_id) if record.workflow_run_id else None,
                # A reusable workflow file (for example `_selected_tests.yaml`)
                # is not the top-level GitHub Actions workflow.  Prefer the CI
                # job history associated with this exact run before considering
                # the raw job id, so the UI can show its real name (such as E2E).
                fallback_workflow_name=(
                    workflow_names_by_ref.get(((record.organization or "").lower(), (record.repository or "").lower(), workflow_file_from_ref(record.workflow_ref) or ""))
                    or workflow_names_by_run_id.get(record.workflow_run_id)
                    or getattr(jobs_by_id.get(_positive_int(record.job_id)), "workflow_name", None)
                ),
                fallback_display_title=pr_titles_by_number.get(record.pr_number) if record.pr_number else None,
                workflow_config_match=((record.organization or "").lower(), (record.repository or "").lower(), workflow_file_from_ref(record.workflow_ref) or "") in workflow_names_by_ref,
            ),
        )
        for record in records
    ]


def parse_names(names: str | None) -> list[str]:
    return [name.strip() for name in (names or "").split(",") if name.strip()]


def _bucket_floor(value: datetime) -> datetime:
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - epoch % STEP_SECONDS, tz=UTC)


class NpuOccupancyService:
    def __init__(self, client: NpuOccupancyClient | None = None, envs: list[dict] | None = None, records: list[OccupancyRecord] | None = None, snapshot_at: datetime | None = None):
        self.client = client or NpuOccupancyClient()
        self.envs = envs
        self.records = records
        self.snapshot_at = snapshot_at

    def options(self, dimension: str) -> list[dict[str, object]]:
        return configured_pool_options() if dimension == "pool" else []

    def _load(self, start: datetime, end: datetime) -> tuple[list[OccupancyRecord], datetime]:
        if self.records is not None:
            return self.records, self.snapshot_at or datetime.now(UTC)
        if self.envs is not None:
            snapshot_at = self.snapshot_at or datetime.now(UTC)
            return normalize_records(self.envs, snapshot_at), snapshot_at
        snapshot_at = datetime.now(UTC)
        envs = self.client.fetch_window(start, end)
        return normalize_records(envs, snapshot_at), snapshot_at

    @staticmethod
    def _selected(records: list[OccupancyRecord], dimension: str, names: list[str]) -> tuple[list[OccupancyRecord], list[str]]:
        values = [record.pool if dimension == "pool" else record.project for record in records]
        available = [pool.name for pool in POOLS] if dimension == "pool" else sorted(dict.fromkeys(values))
        if not names:
            return records, available
        if dimension == "project":
            canonical = {normalize_key(name) for name in names}
            selected = [record for record in records if normalize_key(record.project) in canonical]
        else:
            selected = [record for record in records if record.pool in names]
        return selected, names

    @staticmethod
    def _runs(records: list[OccupancyRecord]) -> list[dict]:
        """Aggregate multiple occupancy environments belonging to one GitHub Run."""
        grouped: dict[str, list[OccupancyRecord]] = defaultdict(list)
        for record in records:
            key = f"run:{record.workflow_run_id}" if record.workflow_run_id else f"env:{record.env_id}"
            grouped[key].append(record)
        rows = []
        for key, items in grouped.items():
            first = items[0]
            rows.append({
                "key": key,
                "workflow_run_id": first.workflow_run_id,
                "workflow_name": first.workflow_name,
                "workflow_file": first.workflow_file,
                "run_display_title": first.run_display_title,
                "run_number": first.run_number,
                "run_display_name": first.run_display_name,
                "workflow_ref": first.workflow_ref,
                "pr_number": first.pr_number,
                "event": first.event,
                "trigger_scope": first.trigger_scope,
                "run_url": first.run_url,
                "github_url": first.github_url,
                "workflow_config_match": first.workflow_config_match,
                "cards": sum(item.cards for item in items),
                "task_count": len(items),
                "created_at": min(item.created_at for item in items),
                "effective_end": max(item.effective_end for item in items),
                "tasks": [item.__dict__ for item in sorted(items, key=lambda item: (item.created_at, item.workflow, item.env_id))],
            })
        return sorted(rows, key=lambda row: (row["created_at"], row["run_display_name"]))

    def trend(self, *, dimension: str, names: list[str], start: datetime, end: datetime) -> dict:
        records, snapshot_at = self._load(start, end)
        records, resolved_names = self._selected(records, dimension, names)
        # A persisted snapshot cannot describe time after it was captured.
        # Stop there rather than rendering those unknown buckets as zero.
        observed_end = min(end, snapshot_at)
        points: list[dict[str, object]] = []
        cursor = _bucket_floor(start)
        while cursor <= observed_end:
            occupied = sum(record.cards for record in records if record.created_at <= cursor < record.effective_end)
            points.append({"timestamp": cursor, "occupied_cards": occupied})
            cursor += timedelta(seconds=STEP_SECONDS)
        values = [point["occupied_cards"] for point in points]
        capacity = None
        # A total that includes unconfigured upstream clusters must never be
        # compared with only the configured pool capacity. Capacity is valid
        # solely for an explicit, fully configured pool selection.
        if dimension == "pool" and names and all(name in POOL_BY_NAME for name in names):
            capacity = sum(POOL_BY_NAME[name].logical_capacity for name in resolved_names if name in POOL_BY_NAME)
        return {
            "dimension": dimension, "names": resolved_names, "start": start, "end": observed_end,
            "sample_interval_seconds": STEP_SECONDS,
            "metrics": {"peak": max(values, default=0), "average": round(sum(values) / len(values), 2) if values else 0, "latest": values[-1] if values else 0, "capacity": capacity},
            "series": points, "snapshot_at": snapshot_at, "data_status": "ready",
        }

    def details(self, *, dimension: str, names: list[str], timestamp: datetime, group_by: str, start: datetime, end: datetime) -> dict:
        records, snapshot_at = self._load(start, end)
        records, resolved_names = self._selected(records, dimension, names)
        groups: dict[str, list[OccupancyRecord]] = defaultdict(list)
        for record in records:
            if record.created_at <= timestamp < record.effective_end:
                groups[record.project if group_by == "project" else record.pool].append(record)
        return {
            "timestamp": timestamp, "dimension": dimension, "names": resolved_names, "group_by": group_by, "snapshot_at": snapshot_at,
            "groups": [
                {
                    "name": name,
                    "occupied_cards": sum(item.cards for item in items),
                    "task_count": len(items),
                    # The UI reads task details from runs[].tasks.  Do not
                    # serialize every task twice at group level.
                    "runs": self._runs(items),
                }
                for name, items in sorted(groups.items())
            ],
        }

    def analysis(self, *, dimension: str, names: list[str], start: datetime, end: datetime) -> dict:
        """Aggregate selected groups over the complete requested interval."""
        records, snapshot_at = self._load(start, end)
        records, resolved_names = self._selected(records, dimension, names)
        # A persisted snapshot cannot describe the future portion of a browser
        # selected range. Use the same observed end as trend(), both for the
        # metrics and the response shown by the analysis table.
        observed_end = min(end, snapshot_at)
        grouped: dict[str, list[OccupancyRecord]] = defaultdict(list)
        for record in records:
            if record.created_at < observed_end and record.effective_end > start:
                grouped[record.pool if dimension == "pool" else record.project].append(record)

        buckets: list[datetime] = []
        cursor = _bucket_floor(start)
        while cursor <= observed_end:
            buckets.append(cursor)
            cursor += timedelta(seconds=STEP_SECONDS)
        groups = []
        for name, items in grouped.items():
            values = [sum(item.cards for item in items if item.created_at <= point < item.effective_end) for point in buckets]
            groups.append({
                "name": name,
                "peak_cards": max(values, default=0),
                "average_cards": round(sum(values) / len(values), 2) if values else 0,
                "task_count": len(items),
                # The UI reads task details from runs[].tasks.  Do not
                # serialize every task twice at group level.
                "runs": self._runs(items),
            })
        return {"dimension": dimension, "names": resolved_names, "start": start, "end": observed_end, "sample_interval_seconds": STEP_SECONDS, "groups": sorted(groups, key=lambda group: (-group["peak_cards"], group["name"])), "snapshot_at": snapshot_at}
