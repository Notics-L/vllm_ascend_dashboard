"""Time overlap checks for the dashboard's five-field image build schedules."""

from calendar import isleap
from datetime import datetime, timedelta
from functools import cache

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.cron.fields import BaseField

from daily_pipeline.cron import schedule_trigger

BUILD_TIMEZONE = "Asia/Shanghai"
_DATE_FIELDS = ("month", "day", "day_of_week")


@cache
def _calendar_dates() -> tuple[datetime, ...]:
    # A Gregorian cycle contains every combination of leap status and January 1
    # weekday. One year per combination covers every month/day/weekday pattern.
    starts: dict[tuple[bool, int], datetime] = {}
    for year in range(2000, 2400):
        january = datetime(year, 1, 1)
        starts.setdefault((isleap(year), january.weekday()), january)
    return tuple(
        january + timedelta(days=offset)
        for (leap, _), january in starts.items()
        for offset in range(366 if leap else 365)
    )


def _rules(trigger: CronTrigger | OrTrigger) -> list[dict[str, BaseField]]:
    rules = trigger.triggers if isinstance(trigger, OrTrigger) else (trigger,)
    return [{field.name: field for field in rule.fields} for rule in rules]


def _matches(field: BaseField, value: datetime) -> bool:
    return field.get_next_value(value) == field.get_value(value)


def schedules_overlap(first: CronTrigger | OrTrigger, second_expression: str) -> bool:
    left = _rules(first)
    right = _rules(schedule_trigger(second_expression, BUILD_TIMEZONE))
    sample = datetime(2000, 1, 1)
    # Month, day, and weekday may be combined with OR by the shared Cron
    # parser. Every rule in that OR shares the same hour and minute fields.
    for name, limit in (("hour", 24), ("minute", 60)):
        points = (sample.replace(**{name: value}) for value in range(limit))
        if not any(_matches(left[0][name], point) and _matches(right[0][name], point) for point in points):
            return False

    return any(
        any(all(_matches(rule[name], date) for name in _DATE_FIELDS) for rule in left)
        and any(all(_matches(rule[name], date) for name in _DATE_FIELDS) for rule in right)
        for date in _calendar_dates()
    )
