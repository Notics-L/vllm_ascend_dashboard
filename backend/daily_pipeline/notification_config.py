"""Persist the notification credential independently of the GitHub credential."""
import json
import logging

from infrastructure.core.security import encrypt_api_key
from infrastructure.persistence.daily_pipeline_settings import has_setting, set_setting
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    FAILURE_NOTIFICATION_RECIPIENTS,
    FAILURE_NOTIFICATION_STAGE,
    failure_notification_recipients,
    prepare_failure_notifications,
)
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineConfig as Config
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from sqlalchemy import select

from daily_pipeline.notifications import wake_notifications

logger = logging.getLogger(__name__)
NOTIFICATION_TOKEN_KEY = "notification_token"


async def notification_config(db):
    return {"tokenConfigured": await has_setting(db, NOTIFICATION_TOKEN_KEY)}


async def save_notification_config(db, token):
    if token and token.strip():
        try:
            await set_setting(db, NOTIFICATION_TOKEN_KEY, encrypt_api_key(token.strip()))
            await db.commit()
        except Exception as exc:
            logger.error("Daily notification configuration save failed error_type=%s", type(exc).__name__)
            raise
        logger.info("Daily notification token configuration saved")
        await wake_notifications(db)
    return await notification_config(db)


async def failure_notification_config(db):
    return {"githubNames": await failure_notification_recipients(db)}


async def save_failure_notification_config(db, github_names):
    recipients = list(dict.fromkeys(name.lower() for name in github_names))
    # Serialize configuration with collection so a failure cannot miss a newly saved list.
    await db.scalar(select(Config.id).where(Config.config_key == "repository").with_for_update())
    await set_setting(db, FAILURE_NOTIFICATION_RECIPIENTS, json.dumps(recipients))
    if recipients:
        rows = (await db.execute(
            select(Failure, Node)
            .join(Node, Node.failure_id == Failure.id)
            .where(
                Failure.processing_status.in_(ACTIVE),
                Node.stage_code == FAILURE_NOTIFICATION_STAGE,
                Node.status == "pending",
            )
            .order_by(Failure.id).with_for_update()
        )).all()
        for failure, node in rows:
            source = await db.get(SourceNode, failure.node_id)
            run = await db.get(Run, source.run_id)
            issue = await db.get(Issue, failure.issue_id) if failure.issue_id else None
            await prepare_failure_notifications(db, failure, node, source, run.repository, issue, recipients)
    await db.commit()
    return {"githubNames": recipients}
