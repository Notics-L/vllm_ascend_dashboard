"""Update Daily recommendations after relevant database changes commit."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from daily_pipeline.source_recommendation import (
    begin_recommendation_round,
    refresh_source_recommendation,
)
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE
from infrastructure.persistence.models import (
    DailyPipelineFailure,
    DailyPipelineIssue,
    DailyPipelineProcessNode,
    DailyPipelineTriggerHistory,
)

logger = logging.getLogger(__name__)
_RECOMMENDATION_CHANGED = "daily_recommendation_changed"
_REQUESTED_ROUND = "daily_recommendation_requested_round"
_RETRY_DELAYS = (5, 20)


def note_recommendation_round(db: AsyncSession, history: DailyPipelineTriggerHistory) -> None:
    """Queue an included trigger after its history commits, without storing the choice there."""
    db.info[_REQUESTED_ROUND] = (history.triggered_at, history.id)


def _changed_recommendation_input(session) -> bool:
    for row in session.dirty:
        if isinstance(row, DailyPipelineFailure):
            state = inspect(row)
            attrs, values = state.attrs, state.dict
            status = attrs.processing_status.history
            category = attrs.problem_category.history
            current_status, current_category = values.get("processing_status"), values.get("problem_category")
            previous_status = status.deleted[0] if status.deleted else current_status
            previous_category = category.deleted[0] if category.deleted else current_category
            previously_active = previous_category == "code" and previous_status in ACTIVE
            currently_active = current_category == "code" and current_status in ACTIVE
            if previously_active != currently_active:
                return True
            if status.has_changes() and (
                "problem_category" not in values or (current_category == "code" and not status.deleted)
            ):
                return True
            if currently_active and attrs.shared_flow_owner_id.history.has_changes():
                return True
        elif isinstance(row, DailyPipelineIssue):
            state = inspect(row).attrs.state.history
            if state.has_changes() and ("closed" in state.added or "closed" in state.deleted):
                return True
        elif isinstance(row, DailyPipelineProcessNode):
            state = inspect(row)
            attrs, values = state.attrs, state.dict
            stage = attrs.stage_code.history
            status = attrs.status.history
            current_stage, current_status = values.get("stage_code"), values.get("status")
            previous_stage = stage.deleted[0] if stage.deleted else current_stage
            previous_status = status.deleted[0] if status.deleted else current_status
            previously_located = previous_stage == "locating" and previous_status == "completed"
            currently_located = current_stage == "locating" and current_status == "completed"
            if previously_located != currently_located:
                return True
            if currently_located and (status.has_changes() or attrs.failure_id.history.has_changes()):
                return True
            if "stage_code" not in values and status.has_changes() and current_status == "completed":
                return True

    return any(
        isinstance(row, DailyPipelineProcessNode)
        and inspect(row).dict.get("stage_code") in (None, "locating")
        and inspect(row).dict.get("status") in (None, "completed")
        for row in session.deleted
    )


@asynccontextmanager
async def watch_daily_recommendations(*, recover: bool = False):
    """Observe commits made by this process and refresh without a recurring job."""
    loop = asyncio.get_running_loop()
    pending = asyncio.Event()
    worker: asyncio.Task[None] | None = None
    requested_round: tuple[datetime, int] | None = None
    closing = False

    async def drain():
        nonlocal requested_round
        while pending.is_set():
            pending.clear()
            for retry_delay in (*_RETRY_DELAYS, None):
                target = requested_round
                try:
                    async with SessionLocal() as db:
                        if target is not None:
                            history = await db.get(DailyPipelineTriggerHistory, target[1])
                            if history is not None:
                                await begin_recommendation_round(db, history)
                            await db.commit()
                            if requested_round == target:
                                requested_round = None
                        complete = await refresh_source_recommendation(db)
                except Exception:
                    logger.exception("Daily recommendation update failed")
                    complete = False
                if complete or pending.is_set():
                    break
                if retry_delay is None:
                    logger.warning("Daily recommendation update still incomplete after retries")
                    if requested_round == target:
                        requested_round = None
                    break
                try:
                    await asyncio.wait_for(pending.wait(), retry_delay)
                except TimeoutError:
                    pass
                else:
                    break

    def request_refresh(target: tuple[datetime, int] | None = None):
        nonlocal worker, requested_round
        if closing:
            return
        if target is not None and (requested_round is None or target > requested_round):
            requested_round = target
        pending.set()
        if worker is None or worker.done():
            worker = asyncio.create_task(drain(), name="daily-source-recommendation")

    def before_flush(session, _context, _instances):
        if not session.info.get(_RECOMMENDATION_CHANGED) and _changed_recommendation_input(session):
            session.info[_RECOMMENDATION_CHANGED] = True

    def after_commit(session):
        if session.in_nested_transaction():
            return
        target = session.info.pop(_REQUESTED_ROUND, None)
        changed = session.info.pop(_RECOMMENDATION_CHANGED, False)
        if target is not None or changed:
            loop.call_soon_threadsafe(request_refresh, target)

    def after_rollback(session):
        if not session.in_nested_transaction():
            session.info.pop(_RECOMMENDATION_CHANGED, None)
            session.info.pop(_REQUESTED_ROUND, None)

    session_class = SessionLocal.class_.sync_session_class
    listeners = (
        ("before_flush", before_flush), ("after_commit", after_commit), ("after_rollback", after_rollback),
    )
    for name, callback in listeners:
        event.listen(session_class, name, callback)
    try:
        if recover:
            request_refresh()
        yield
    finally:
        closing = True
        for name, callback in listeners:
            event.remove(session_class, name, callback)
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
