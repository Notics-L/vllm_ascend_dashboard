from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

# The dashboard uses crontab numbering (0 and 7 are Sunday).
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun")


def weekday_names(field: str) -> str:
    if field == "*":
        return field
    days = set()
    for item in field.split(","):
        span, slash, stride = item.partition("/")
        step = int(stride) if slash else 1
        if span == "*":
            first, last = 0, 7
        else:
            start, dash, end = span.partition("-")
            first = int(start)
            last = int(end) if dash else (7 if slash else first)
        if not 0 <= first <= last <= 7 or step < 1:
            raise ValueError(f"Invalid Cron weekday: {item}")
        days.update(WEEKDAYS[n] for n in range(first, last + 1, step))
    return ",".join(sorted(days))


def schedule_trigger(expression: str, timezone: str):
    minute, hour, day, month, weekday = expression.split()
    fields = {"minute": minute, "hour": hour, "month": month, "timezone": timezone}
    weekday_filter = weekday_names(weekday)
    if not day.startswith("*") and not weekday.startswith("*"):
        # crontab fires on either matching day when both day fields are restricted.
        return OrTrigger([
            CronTrigger(day=day, **fields),
            CronTrigger(day_of_week=weekday_filter, **fields),
        ])
    return CronTrigger(day=day, day_of_week=weekday_filter, **fields)
