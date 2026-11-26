"""Persist Daily execution trees as an atomic, repeatable snapshot."""
import re
from collections import Counter, defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from infrastructure.persistence.daily_pipeline_cases import DailyCaseRepository
from infrastructure.persistence.daily_pipeline_tracking import ingest_snapshot
from infrastructure.persistence.models import (
    DailyPipelineCollectionNode as Node,
)
from infrastructure.persistence.models import (
    DailyPipelineCollectionRun as Run,
)
from infrastructure.persistence.models import (
    DailyPipelineTriggerHistory as History,
)

RUN_FIELDS = (
    "run_id", "workflow", "result", "started_at", "finished_at",
    "last_collected_at", "issue_number", "issue_state",
)
NODE_FIELDS = (
    "node_type", "source_id", "name", "result", "case_type", "hardware", "vllm_version",
)
RESULTS = {"queued", "running", "success", "failure", "cancelled", "skipped"}
NODE_TYPES = {"workflow_run", "matrix", "workflow_call", "job", "test_job"}
CASE_TYPES = {"Daily", "UT", "E2E", "UT/E2E"}
COUNTED_CASE_RESULTS = {"success", "failure"}


def _positive_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Collection IDs must be positive integers")
    return value


def _snapshot_rows(run_id: int, snapshot: dict) -> tuple[dict, dict, list[str]]:
    """Validate the whole tree before writing, including cycles and ownership."""
    root = {key: snapshot["run"].get(key) for key in RUN_FIELDS}
    _positive_id(root["run_id"])
    if root["run_id"] != run_id or root["result"] not in RESULTS or not root["workflow"]:
        raise ValueError("Collection snapshot must contain its main Daily run")
    nodes = {}
    native_ids = set()
    for item in snapshot["nodes"]:
        key = item.get("ref")
        if not isinstance(key, str) or not key or key in nodes:
            raise ValueError("Snapshot node references must be nonempty and unique")
        kind = item.get("node_type")
        if kind not in NODE_TYPES or item.get("result") not in RESULTS or not isinstance(item.get("name"), str) or not item["name"]:
            raise ValueError("Collection node type, name, or result is invalid")
        source_id = item.get("source_id")
        if source_id is not None:
            _positive_id(source_id)
            native = ("run" if kind == "workflow_run" else "job", source_id)
            if native in native_ids:
                raise ValueError("A GitHub execution cannot appear twice in the tree")
            native_ids.add(native)
        if kind in {"workflow_run", "job", "test_job"} and source_id is None:
            raise ValueError("Actual workflow and job nodes require their GitHub ID")
        if kind in {"matrix", "workflow_call"} and source_id is not None:
            raise ValueError("Logical job groups must not fabricate a GitHub ID")
        if kind == "workflow_run" and (item.get("parent_ref") is not None or source_id == run_id):
            raise ValueError("Nightly workflows must be direct children of the Daily root")
        if kind == "test_job":
            if item.get("case_type") not in CASE_TYPES:
                raise ValueError("Test nodes require a test type")
        elif any(item.get(field) is not None for field in ("case_type", "hardware", "vllm_version")):
            raise ValueError("Only test nodes may contain test attributes")
        nodes[key] = {**{field: item.get(field) for field in NODE_FIELDS}, "parent_ref": item.get("parent_ref"), "execution_started_at": item.get("execution_started_at"), "failed_at": item.get("failed_at")}
    # Kahn ordering validates missing parents and cycles without recursion limits.
    children = {key: [] for key in nodes}
    ready = []
    sibling_groups = set()
    for key, node in nodes.items():
        parent = node["parent_ref"]
        if parent is not None and not isinstance(parent, str):
            raise ValueError("Snapshot parent references must be strings")
        if node["source_id"] is None:
            identity = (parent, node["node_type"], node["name"])
            if identity in sibling_groups:
                raise ValueError("Duplicate aggregate node under the same parent")
            sibling_groups.add(identity)
        if node["node_type"] == "test_job":
            allowed = {"matrix", "workflow_call", "workflow_run"} if node["case_type"] == "Daily" else {"matrix", "workflow_call"}
            if parent is None or parent not in nodes or nodes[parent]["node_type"] not in allowed:
                raise ValueError("Test node must belong to its calling group or Nightly workflow")
        if parent is None:
            ready.append(key)
        elif parent not in nodes or parent == key or nodes[parent]["node_type"] == "test_job":
            raise ValueError("Collection node has an invalid parent")
        else:
            children[parent].append(key)
    ordered = []
    for key in ready:
        ordered.append(key)
        ready.extend(children[key])
    if len(ordered) != len(nodes):
        raise ValueError("Collection node tree contains a cycle")
    workflow_owners = {}
    for key in ordered:
        node = nodes[key]
        parent = node["parent_ref"]
        workflow_owners[key] = key if node["node_type"] == "workflow_run" else workflow_owners.get(parent)
        if node["node_type"] == "test_job":
            if (node["case_type"] == "Daily") != (workflow_owners[key] is not None):
                raise ValueError("Test type does not match its owning workflow")
    return root, nodes, ordered


def _node_identity(node_type, source_id, parent_id, name):
    """Match remote executions by ID and aggregates by their tree location."""
    if source_id is not None:
        namespace = "workflow_run" if node_type == "workflow_run" else "github_job"
        return namespace, source_id
    return "aggregate", parent_id, node_type, name


class DailyCollectionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def ensure_root(self, run_id: int, workflow: str, *, commit: bool = True) -> Run:
        _positive_id(run_id)
        statement = insert(Run).values(run_id=run_id, workflow=workflow, result="queued")
        await self.db.execute(statement.on_duplicate_key_update(run_id=statement.inserted.run_id))
        row = await self.db.scalar(
            select(Run).where(Run.run_id == run_id, Run.deleted_at.is_(None)).execution_options(populate_existing=True),
        )
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return row

    async def list_roots(self) -> list[Run]:
        return list(await self.db.scalars(select(Run).where(Run.deleted_at.is_(None)).order_by(Run.run_id.desc())))

    async def get_root(self, run_id: int) -> Run | None:
        return await self.db.scalar(select(Run).where(Run.run_id == run_id, Run.deleted_at.is_(None)))

    async def history_links(self, run_ids: set[int]) -> dict[int, tuple[int, str]]:
        if not run_ids:
            return {}
        latest_ids = (
            select(func.max(History.id))
            .where(History.run_id.in_(run_ids))
            .group_by(History.run_id)
        )
        rows = await self.db.execute(
            select(History.run_id, History.id, History.repository).where(History.id.in_(latest_ids)),
        )
        return {run_id: (history_id, repository) for run_id, history_id, repository in rows}

    async def get_tree(self, run_id: int) -> dict | None:
        root = await self.get_root(run_id)
        if root is None:
            return None
        nodes = list(await self.db.scalars(
            select(Node).where(Node.run_id == run_id, Node.is_current.is_(True)).order_by(Node.id),
        ))
        history = await self.db.scalar(
            select(History).where(History.run_id == run_id).order_by(History.id.desc()).limit(1),
        )
        return {"run": root, "nodes": nodes, "history": history}

    async def issue_history(self, number: int) -> History | None:
        return await self.db.scalar(
            select(History).join(Run, Run.run_id == History.run_id)
            .where(Run.issue_number == number).order_by(History.id.desc()).limit(1),
        )

    async def update_issue_state(self, number: int, state: str) -> None:
        await self.db.execute(update(Run).where(Run.issue_number == number).values(issue_state=state))
        await self.db.commit()

    async def pending_run_ids(self) -> list[int]:
        merged_prs_pending = select(History.id).where(
            History.run_id == Run.run_id, History.merged_prs_synced.is_(False),
        ).exists()
        return list(await self.db.scalars(
            select(Run.run_id)
            .where(Run.deleted_at.is_(None), Run.finished_at.is_(None) | merged_prs_pending)
            .order_by(Run.run_id)
        ))

    async def delete_roots(self, run_ids: set[int]) -> int:
        if not run_ids:
            return 0
        ids = list(await self.db.scalars(
            select(Run.run_id).where(Run.run_id.in_(run_ids), Run.deleted_at.is_(None)).order_by(Run.run_id).with_for_update(),
        ))
        if ids:
            await self.db.execute(
                update(Run).where(Run.run_id.in_(ids)).values(deleted_at=func.utc_timestamp()).execution_options(synchronize_session=False),
            )
        if ids:
            await self.db.execute(update(Node).where(Node.run_id.in_(ids)).values(is_current=False))
        await self.db.commit()
        return len(ids)

    @asynccontextmanager
    async def collection_lease(self, run_id: int) -> AsyncIterator[bool]:
        """Keep the remote collection lease on a dedicated MySQL connection."""
        _positive_id(run_id)
        bind = self.db.bind
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        if not isinstance(engine, AsyncEngine):
            raise RuntimeError("Daily collection requires an asynchronous database engine")
        params = {"name": f"daily_pipeline_collection:{run_id}"}
        async with engine.connect() as connection:
            try:
                acquired = await connection.scalar(text("SELECT GET_LOCK(:name, 0)"), params)
            except BaseException:
                # The server may have acquired the lock before a cancelled
                # request lost its response, so this connection cannot be pooled.
                await connection.invalidate()
                raise
            if acquired == 0:
                yield False
                return
            if acquired != 1:
                await connection.invalidate()
                raise RuntimeError("Could not acquire Daily collection lease")
            try:
                yield True
            finally:
                try:
                    released = await connection.scalar(text("SELECT RELEASE_LOCK(:name)"), params)
                    if released != 1:
                        await connection.invalidate()
                except BaseException:
                    # A failed release must never put a still-locked connection
                    # back into the pool for an unrelated request to reuse.
                    await connection.invalidate()
                    raise

    async def apply_snapshot(self, run_id: int, snapshot: dict) -> bool:
        """Match natural identities, retaining IDs while replacing the tree."""
        _positive_id(run_id)
        values, nodes, ordered = _snapshot_rows(run_id, snapshot)
        try:
            root = await self.db.scalar(
                select(Run).where(Run.run_id == run_id, Run.deleted_at.is_(None)).with_for_update()
                .execution_options(populate_existing=True),
            )
            if root is None:
                await self.db.rollback()
                return False
            original_rows = list(await self.db.scalars(
                select(Node).where(Node.run_id == run_id).with_for_update()
                .execution_options(populate_existing=True),
            ))
            history = await self.db.scalar(select(History).where(History.run_id == run_id).order_by(History.id.desc()).limit(1))
            if history:
                root.repository, root.source_branch = history.repository, history.source_branch
            existing = {}
            for row in original_rows:
                row.is_current = False
                identity = _node_identity(row.node_type, row.source_id, row.parent_id, row.name)
                if identity in existing:
                    raise ValueError("Existing collection nodes have ambiguous identities")
                existing[identity] = row
            resolved = {}
            desired_identities = set()
            counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
            # Temporary references only connect incoming snapshot nodes.
            # They never participate in matching stored rows.
            # Retain omitted nodes as history; expose only the current snapshot.
            for ref in ordered:
                item = nodes[ref]
                parent_ref = item["parent_ref"]
                parent_id = resolved[parent_ref].id if parent_ref is not None else None
                identity = _node_identity(item["node_type"], item["source_id"], parent_id, item["name"])
                if identity in desired_identities:
                    raise ValueError("Snapshot nodes have ambiguous identities")
                desired_identities.add(identity)
                row = existing.get(identity)
                if (
                    item["node_type"] == "test_job"
                    and item["case_type"] == "Daily"
                    and item["hardware"]
                    and item["result"] in COUNTED_CASE_RESULTS
                    and (
                        row is None
                        or row.case_type != "Daily"
                        or row.result not in COUNTED_CASE_RESULTS
                    )
                ):
                    counts[(item["name"], item["hardware"].lower())][item["result"]] += 1
                fields = {field: item[field] for field in NODE_FIELDS}
                if row is None:
                    row = Node(run_id=run_id, parent_id=parent_id, **fields)
                    self.db.add(row)
                else:
                    row.parent_id = parent_id
                    for field, value in fields.items():
                        setattr(row, field, value)
                row.is_current = True
                row.execution_started_at = item.get("execution_started_at") or row.execution_started_at
                row.failed_at = item.get("failed_at") or row.failed_at
                row.source_run_id = run_id
                row.vllm_ref = item.get("vllm_version")
                ancestor_ref = parent_ref
                while ancestor_ref:
                    ancestor = nodes[ancestor_ref]
                    if ancestor["node_type"] == "workflow_run":
                        row.source_run_id = ancestor["source_id"]
                    version = re.fullmatch(r"run-all-tests \(([^)]+)\)", ancestor["name"])
                    if version:
                        row.vllm_ref = version.group(1)
                    ancestor_ref = ancestor["parent_ref"]
                await self.db.flush()
                resolved[ref] = row
            for field, value in values.items():
                setattr(root, field, value)
            if root.pending_reruns and snapshot.get("complete"):
                root.pending_reruns = None
            await ingest_snapshot(self.db, run_id, snapshot, resolved)
            if history and counts:
                await DailyCaseRepository(self.db).add_execution_counts(counts)
            await self.db.commit()
            return True
        except BaseException:
            await self.db.rollback()
            raise
