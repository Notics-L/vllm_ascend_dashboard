"""Configuration shared by the Daily trigger form and the schedule preview."""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daily_pipeline.cron import schedule_trigger
from infrastructure.clients.daily_pipeline_config import check_trigger_permission
from infrastructure.clients.daily_pipeline_scheduler import SchedulerUpdateError, update_schedule
from infrastructure.core.github_credentials import daily_github_credential
from infrastructure.core.security import encrypt_api_key
from infrastructure.persistence.daily_pipeline_schedules import (
    delete_schedule,
    read_schedule,
    save_schedule,
)
from infrastructure.persistence.daily_pipeline_settings import (
    has_setting,
    read_settings,
    set_setting,
)

logger = logging.getLogger(__name__)
TRIGGER_TASK = "trigger_daily_pipeline"
DEFAULT_TIMEZONE = "Asia/Shanghai"


async def trigger_config(db: AsyncSession):
    token_configured = await has_setting(db, "github_token")
    schedule = await read_schedule(db, TRIGGER_TASK)
    return {"githubTokenConfigured": token_configured, "cron": schedule[0] if schedule else None}


async def check_trigger_connection(db: AsyncSession, token: str | None):
    keys = ("repository",) if token else ("repository", "github_token")
    settings = await read_settings(db, *keys)
    credential = daily_github_credential(settings, candidate=token)
    if not credential:
        raise ValueError("请输入 GitHub Token")
    credential.validate()
    repository = settings.get("repository")
    if not repository:
        raise ValueError("请先配置目标仓库")
    verified, detail = await check_trigger_permission(repository, credential)
    return {"verified": verified, "detail": detail}


async def save_trigger_config(db: AsyncSession, cron: str | None, token: str | None):
    cron = (cron or "").strip()
    try:
        if cron:
            schedule = await read_schedule(db, TRIGGER_TASK)
            timezone = schedule[1] if schedule else DEFAULT_TIMEZONE
            schedule_trigger(cron, timezone)
            await save_schedule(db, TRIGGER_TASK, cron, timezone)
        else:
            await delete_schedule(db, TRIGGER_TASK)
        if token:
            await set_setting(db, "github_token", encrypt_api_key(token))
        await db.commit()
    except Exception as exc:
        # Database exceptions can contain the encrypted Token in SQL parameters.
        logger.error("Daily trigger configuration save failed: error_type=%s", type(exc).__name__)
        raise
    logger.info("Daily trigger configuration saved: cron=%s token_changed=%s", cron, bool(token))
    try:
        await update_schedule(TRIGGER_TASK)
    except SchedulerUpdateError:
        logger.error("Daily schedule notification failed after commit task_key=%s", TRIGGER_TASK)
        raise
    return await trigger_config(db)


def preview_schedule(cron: str | None, timezone: str = DEFAULT_TIMEZONE):
    times = []
    upcoming = []
    if cron:
        trigger = schedule_trigger(cron, timezone)
        now = datetime.now(ZoneInfo(timezone))
        point = trigger.get_next_fire_time(None, now)
        for _ in range(5):
            if point is None:
                break
            upcoming.append(point.strftime("%Y-%m-%d %H:%M"))
            point = trigger.get_next_fire_time(point, point + timedelta(seconds=1))
        minute, hour, *_ = cron.split()
        clock = schedule_trigger(f"{minute} {hour} * * *", timezone)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        point = clock.get_next_fire_time(None, start)
        while point and point.date() == start.date():
            times.append(point.strftime("%H:%M"))
            point = clock.get_next_fire_time(point, point + timedelta(seconds=1))
    return {"cron": cron, "timeZone": timezone, "executionTimes": upcoming, "timeOptions": times}
