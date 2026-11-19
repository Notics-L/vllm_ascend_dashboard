from collections import Counter
from contextlib import asynccontextmanager

from sqlalchemy import delete, select, text, tuple_, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.daily_pipeline_settings import read_settings
from infrastructure.persistence.models import DailyPipelineTestCase as Case


class DailyCaseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def active_failure_ids(self):
        from infrastructure.persistence.models import DailyPipelineCollectionNode as Node
        from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
        from infrastructure.persistence.models import DailyPipelineFailure as Failure
        settings = await read_settings(self.db, "repository")
        rows = await self.db.execute(select(Node.name, Node.hardware, Failure.id).join(Failure, Failure.node_id == Node.id).join(Run, Run.run_id == Node.run_id).where(Run.repository == settings.get("repository"), Node.case_type == "Daily", Failure.processing_status.in_(("pending", "running", "unresolved"))).order_by(Failure.discovered_at, Failure.id))
        return {(name, (hardware or "").lower()): str(identity) for name, hardware, identity in rows}

    async def catalog_source(self):
        return await read_settings(self.db, "repository", "source_branch", "nightly_config_path")

    async def has_cases(self, *, for_update: bool = False) -> bool:
        query = select(Case.name).limit(1)
        if for_update:
            query = query.with_for_update()
        return await self.db.scalar(query) is not None

    async def list_cases(self, *, is_daily: bool | None = None, for_update: bool = False):
        query = select(Case).order_by(Case.name, Case.hardware)
        if is_daily is not None:
            query = query.where(Case.is_daily.is_(is_daily))
        if for_update:
            query = query.with_for_update()
        return (await self.db.scalars(query)).all()

    async def lock_daily_cases(self, keys: set[str]):
        identities = []
        for key in keys:
            hardware, _, name = key.partition(":")
            identities.append((name, hardware))
        query = (
            select(Case)
            .where(Case.is_daily.is_(True), tuple_(Case.name, Case.hardware).in_(identities))
            .order_by(Case.name, Case.hardware)
            .with_for_update()
        )
        return (await self.db.scalars(query)).all()

    @asynccontextmanager
    async def catalog_refresh_lock(self):
        # GET_LOCK is connection-scoped; release it before returning the session
        # connection to the pool. Locking reads see the previous writer's commit.
        lock = {"name": "daily_pipeline_test_cases:refresh"}
        acquired = await self.db.scalar(text("SELECT GET_LOCK(:name, 30)"), lock)
        if acquired != 1:
            raise TimeoutError("Timed out waiting for Daily case catalog refresh")
        try:
            yield
        finally:
            await self.db.execute(text("SELECT RELEASE_LOCK(:name)"), lock)
        await self.db.commit()

    async def sync_catalog(self, branch: str, definitions: dict[tuple[str, str], str]):
        rows = [
            {"name": name, "hardware": hardware, "source_branch": branch, "test_type": section}
            for (name, hardware), section in sorted(definitions.items())
        ]
        if rows:
            stmt = insert(Case).values(rows)
            await self.db.execute(stmt.on_duplicate_key_update(
                test_type=stmt.inserted.test_type, source_branch=stmt.inserted.source_branch,
            ))
        obsolete = delete(Case).where(Case.source_branch == branch)
        if rows:
            obsolete = obsolete.where(tuple_(Case.name, Case.hardware).not_in(list(definitions)))
        await self.db.execute(obsolete)
        from infrastructure.persistence.daily_pipeline_tracking import reconcile_case_availability
        await reconcile_case_availability(self.db)

    async def add_execution_counts(self, counts: dict[tuple[str, str], Counter[str]]) -> None:
        for (name, hardware), results in sorted(counts.items()):
            await self.db.execute(
                update(Case)
                .where(Case.name == name, Case.hardware == hardware)
                .values(
                    success_count=Case.success_count + results["success"],
                    failure_count=Case.failure_count + results["failure"],
                )
            )

    async def reconcile_availability(self, rows):
        from infrastructure.persistence.daily_pipeline_tracking import reconcile_case_availability
        await reconcile_case_availability(self.db, rows)

    async def commit(self):
        await self.db.commit()
