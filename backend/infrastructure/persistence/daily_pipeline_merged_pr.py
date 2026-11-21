"""Persistence for pull requests merged between Daily triggers."""

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.mysql import insert

from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineMergedPR as MergedPR
from infrastructure.persistence.models import DailyPipelineTriggerHistory as History


async def link_merged_prs_to_issue(db, repository, run_id, issue_id):
    history_matches = (History.repository == repository, History.run_id == run_id)
    history_ids = select(History.id).where(*history_matches)
    linked = await db.execute(
        update(MergedPR)
        .where(MergedPR.trigger_history_id.in_(history_ids), MergedPR.issue_id.is_(None))
        .values(issue_id=issue_id)
    )
    if linked.rowcount:
        await db.execute(
            update(History).where(*history_matches).values(merged_prs_synced=False)
        )


class DailyMergedPRRepository:
    def __init__(self, db):
        self.db = db

    async def synced(self, history_id):
        return await self.db.scalar(select(History.merged_prs_synced).where(History.id == history_id))

    async def set_synced(self, history_id, synced):
        await self.db.execute(
            update(History).where(History.id == history_id).values(merged_prs_synced=synced)
        )
        await self.db.commit()

    async def previous_trigger_at(self, history):
        cutoff = tuple_(History.triggered_at, History.id) < tuple_(history["triggered_at"], history["id"])
        return await self.db.scalar(
            select(History.triggered_at).where(
                History.repository == history["repository"],
                History.source_branch == history["source_branch"],
                History.workflow == history["workflow"],
                History.run_id.is_not(None),
                History.run_id != history["run_id"],
                cutoff,
            )
            .order_by(History.triggered_at.desc(), History.id.desc())
            .limit(1)
        )

    async def pulls(self, history_id):
        return (await self.db.execute(
            select(MergedPR.pr_number, MergedPR.title, MergedPR.url)
            .where(MergedPR.trigger_history_id == history_id)
            .order_by(MergedPR.merged_at, MergedPR.pr_number)
        )).all()

    async def save_pulls(self, history, pulls):
        # Match the run lock used when an Issue is attached to this window.
        run_id = await self.db.scalar(
            select(Run.run_id).where(Run.run_id == history["run_id"], Run.deleted_at.is_(None)).with_for_update()
        )
        if run_id is None:
            await self.db.rollback()
            return
        issue_id = await self.db.scalar(select(Issue.id).where(
            Issue.repository == history["repository"], Issue.run_id == run_id,
        ))
        statement = insert(MergedPR).values([
            {**pull, "trigger_history_id": history["id"], "issue_id": issue_id}
            for pull in pulls
        ])
        await self.db.execute(statement.on_duplicate_key_update(
            title=statement.inserted.title,
            author=statement.inserted.author,
            merged_by=statement.inserted.merged_by,
            url=statement.inserted.url,
            merged_at=statement.inserted.merged_at,
            issue_id=statement.inserted.issue_id,
        ))
        await self.db.commit()

    async def fill_pull_metadata(self, history_id, metadata_by_number):
        for number, metadata in metadata_by_number.items():
            await self.db.execute(update(MergedPR).where(
                MergedPR.trigger_history_id == history_id,
                MergedPR.pr_number == number,
                MergedPR.url.is_(None),
            ).values(**metadata))
        await self.db.commit()

    async def issue_number(self, history):
        return await self.db.scalar(
            select(Issue.number).join(MergedPR, MergedPR.issue_id == Issue.id)
            .where(
                MergedPR.trigger_history_id == history["id"],
                Issue.repository == history["repository"],
                Issue.run_id == history["run_id"],
                Issue.deleted_at.is_(None),
            ).limit(1)
        )
