"""Persistence for the Daily notification recipient directory."""

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE, NOTIFICATION_STAGES, STANDALONE_NOTICE_PURPOSES, VERSION_BUILD_NOTICE,
)
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineNotificationRecipient as Recipient
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution


class DailyNotificationRecipientRepository:
    def __init__(self, db):
        self.db = db

    async def list(self):
        return list(await self.db.scalars(select(Recipient).order_by(Recipient.github_name)))

    async def get(self, identity):
        return await self.db.get(Recipient, identity)

    async def by_github_name(self, github_name):
        return await self.db.scalar(
            select(Recipient).where(Recipient.github_name == github_name.strip().lower())
        )

    def add(self, github_name, name, encrypted_account):
        row = Recipient(
            github_name=github_name.strip().lower(), name=name, account=encrypted_account
        )
        self.db.add(row)
        return row

    async def commit(self):
        await self.db.commit()

    async def rollback(self):
        await self.db.rollback()

    async def delete(self, row):
        await self.db.delete(row)


class DailyNotificationOutboxRepository:
    """Use process executions as the outbox; the failure lock serializes all transitions."""

    def __init__(self, db):
        self.db = db

    async def candidate_failures(self):
        purpose = Execution.request_data["purpose"].as_string()
        return list(await self.db.scalars(
            select(Failure.id)
            .join(Node, Node.failure_id == Failure.id)
            .join(Execution, Execution.node_id == Node.id)
            .where(
                *self.ready_deliveries(),
                or_(
                    purpose.in_(STANDALONE_NOTICE_PURPOSES),
                    and_(Failure.processing_status.in_(ACTIVE), Node.stage_code.in_(NOTIFICATION_STAGES), Node.status == "waiting"),
                ),
            )
            .order_by(Failure.id).distinct().limit(100)
        ))

    @staticmethod
    def ready_deliveries():
        purpose = Execution.request_data["purpose"].as_string()
        member = aliased(Failure)
        version_active = (
            select(member.id).select_from(member).outerjoin(Issue, Issue.id == member.issue_id)
            .where(
                member.version_flow_id == Failure.version_flow_id,
                member.processing_status.in_(ACTIVE),
                or_(member.issue_id.is_(None), and_(Issue.state == "open", Issue.deleted_at.is_(None))),
            ).correlate(Failure).exists()
        )
        return (
            Execution.status == "waiting",
            Execution.source == "auto",
            Execution.request_data["delivery_mode"].as_string() == "pull",
            Execution.request_data["ready"].as_boolean().is_(True),
            Execution.request_data["delivery_started"].as_boolean().is_(False),
            or_(purpose.is_(None), purpose != VERSION_BUILD_NOTICE, version_active),
        )

    async def standalone_delivery(self, failure_id):
        return await self.db.scalar(
            select(Execution).join(Node, Node.id == Execution.node_id)
            .join(Failure, Failure.id == Node.failure_id)
            .where(
                Node.failure_id == failure_id,
                Execution.request_data["purpose"].as_string().in_(STANDALONE_NOTICE_PURPOSES),
                *self.ready_deliveries(),
            )
            .order_by(Execution.id).limit(1)
            .execution_options(populate_existing=True)
        )

    async def lock_failure(self, identity, *, skip_locked=False):
        return await self.db.scalar(
            select(Failure).where(Failure.id == identity)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )

    async def nodes(self, failure_id):
        return list(await self.db.scalars(
            select(Node).where(Node.failure_id == failure_id).order_by(Node.id)
            .execution_options(populate_existing=True)
        ))

    async def latest_execution(self, node_id):
        return await self.db.scalar(
            select(Execution).where(Execution.node_id == node_id)
            .order_by(Execution.attempt_no.desc()).limit(1)
            .execution_options(populate_existing=True)
        )

    async def execution(self, identity):
        return await self.db.get(Execution, identity)

    async def node(self, identity):
        return await self.db.get(Node, identity)

    async def recipient(self, github_name):
        return await DailyNotificationRecipientRepository(self.db).by_github_name(github_name)


    async def consumption_records(self, page, page_size, outcome=None, status=None):
        delivery_result = case(
            (Execution.status == "ended", "ended"),
            else_=func.coalesce(Execution.result_data["delivery_status"].as_string(), "waiting"),
        )
        consumption_status = case(
            (Execution.status == "ended", "ended"),
            (Execution.result_data["delivery_status"].as_string().in_(("sent", "failed")), "completed"),
            (Execution.request_data["delivery_started"].as_boolean().is_(True), "processing"),
            else_="pending",
        )
        filters = [
            or_(Node.stage_code.in_((*NOTIFICATION_STAGES, VERSION_BUILD_NOTICE)),
                Execution.request_data["purpose"].as_string() == "revert_deadline"),
            Execution.source == "auto",
            Execution.request_data["delivery_mode"].as_string() == "pull",
            Execution.request_data["ready"].as_boolean().is_(True),
        ]
        if outcome:
            filters.append(delivery_result == outcome)
        if status:
            filters.append(consumption_status == status)
        query = select(Execution, Node.stage_code).join(Node, Node.id == Execution.node_id).where(*filters)
        total = await self.db.scalar(select(func.count()).select_from(query.subquery()))
        rows = (await self.db.execute(
            query.order_by(Execution.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )).all()
        return rows, total
