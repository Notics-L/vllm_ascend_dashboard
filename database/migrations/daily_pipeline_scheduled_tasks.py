"""Create the Cron schedule table and upgrade the original interval-based schema."""
from infrastructure.db.base import engine
from infrastructure.persistence.models import DailyPipelineScheduledTask
from sqlalchemy import inspect, text

from database.migrations.task_queue import MIGRATION_HISTORY_DDL

MIGRATION_VERSION = "20261116_daily_pipeline_scheduled_tasks"
TABLE = "daily_pipeline_scheduled_tasks"


def interval_cron(minutes: int) -> str:
    if minutes == 60:
        return "0 * * * *"
    if 0 < minutes < 60 and 60 % minutes == 0:
        return f"*/{minutes} * * * *"
    if minutes == 1440:
        return "0 0 * * *"
    if 60 < minutes < 1440 and minutes % 60 == 0 and 1440 % minutes == 0:
        return f"0 */{minutes // 60} * * *"
    raise ValueError(f"Cannot represent a {minutes}-minute interval with a single daily Cron rule")


async def upgrade_intervals(conn) -> None:
    columns = await conn.run_sync(lambda c: inspect(c).get_columns(TABLE))
    if not any(col["name"] == "interval_minutes" for col in columns):
        return

    intervals = await conn.scalars(text(f"""
        SELECT DISTINCT interval_minutes FROM {TABLE}
        WHERE trigger_type = 'interval' AND cron_expression IS NULL
    """))
    conversions = [(minutes, interval_cron(minutes)) for minutes in intervals]
    checks = await conn.run_sync(lambda c: inspect(c).get_check_constraints(TABLE))
    old_checks = {"ck_daily_pipeline_task_schedule", "ck_daily_catalog_interval"}
    drops = [f"DROP CHECK {check['name']}" for check in checks if check["name"] in old_checks]
    if drops:
        await conn.execute(text(f"ALTER TABLE {TABLE} {', '.join(drops)}"))
    for minutes, cron in conversions:
        await conn.execute(text(f"""
            UPDATE {TABLE} SET cron_expression = :cron
            WHERE interval_minutes = :minutes AND cron_expression IS NULL
        """), {"cron": cron, "minutes": minutes})
    await conn.execute(text(f"""
        ALTER TABLE {TABLE}
        DROP COLUMN interval_minutes, DROP COLUMN trigger_type,
        MODIFY cron_expression VARCHAR(128) NOT NULL,
        ADD CONSTRAINT ck_daily_task_cron CHECK (LENGTH(TRIM(cron_expression)) > 0)
    """))


async def run() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(MIGRATION_HISTORY_DDL))
        await conn.run_sync(DailyPipelineScheduledTask.__table__.create, checkfirst=True)
        await upgrade_intervals(conn)
        params = {"version": MIGRATION_VERSION}
        # Repair missing default system tasks even after this migration was recorded.
        # Preserve custom schedules, timezone, and deliberately disabled tasks.
        await conn.execute(text("""
            INSERT INTO daily_pipeline_scheduled_tasks (task_key, cron_expression)
            VALUES ('sync_nightly_cases', '0 * * * *'),
                   ('sync_daily_issues', '*/5 * * * *'),
                   ('advance_daily_processing', '* * * * *')
            ON DUPLICATE KEY UPDATE task_key = task_key
        """))
        await conn.execute(text("""
            INSERT INTO migration_history (version) VALUES (:version)
            ON DUPLICATE KEY UPDATE version = version
        """), params)
