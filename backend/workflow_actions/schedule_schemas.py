"""Workflow Actions 定时任务请求模型。"""

from datetime import UTC, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, field_validator

from workflow_actions.schemas import normalize_workflow_path

SCHEDULE_TIMEZONE = "Asia/Shanghai"


def cron_trigger(expression: str, timezone: str = SCHEDULE_TIMEZONE) -> CronTrigger:
    try:
        return CronTrigger.from_crontab(expression, timezone=timezone)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Cron 表达式无效：{exc}") from exc


def cron_preview(expression: str) -> str:
    minute, hour, day, month, weekday = expression.split()
    if weekday != "*":
        # Render APScheduler's Monday-first weekdays as names for the UI formatter.
        trigger = CronTrigger(day_of_week=weekday, timezone=UTC)
        monday = datetime(2024, 1, 1, tzinfo=UTC)
        weekdays = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        matching_days = []
        for offset in range(len(weekdays)):
            date = monday + timedelta(days=offset)
            if trigger.get_next_fire_time(None, date) == date:
                matching_days.append(offset)
        if len(matching_days) == 7:
            weekday = "*"
        elif len(matching_days) > 2 and matching_days[-1] - matching_days[0] + 1 == len(matching_days):
            weekday = f"{weekdays[matching_days[0]]}-{weekdays[matching_days[-1]]}"
        else:
            weekday = ",".join(weekdays[offset] for offset in matching_days)
    return " ".join((minute, hour, day.lower().replace("last", "L"), month, weekday))


class WorkflowScheduleBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workflow_path: str = Field(min_length=1, max_length=500)
    ref: str = Field(default="main", min_length=1, max_length=200)
    inputs_yaml: str = ""
    cron_expression: str = Field(min_length=5, max_length=100)
    enabled: bool = True

    @field_validator("name", "ref")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value

    @field_validator("workflow_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_workflow_path(value)

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        value = " ".join(value.split())
        cron_trigger(value)
        return value


class WorkflowScheduleCreate(WorkflowScheduleBase):
    pass


class WorkflowScheduleUpdate(WorkflowScheduleBase):
    pass


class WorkflowScheduleEnabledUpdate(BaseModel):
    enabled: bool
