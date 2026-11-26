"""Add a persistent source ref selection mode to image build tasks."""

from sqlalchemy import inspect, text

from infrastructure.db.base import engine

MIGRATION_VERSION = "20261126_03_image_build_source_ref_mode"
TABLE = "image_build_trigger_tasks"


async def run() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns(TABLE))
        mode = next((column for column in columns if column["name"] == "source_ref_mode"), None)
        if mode is None:
            await conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN source_ref_mode VARCHAR(16) NULL"))
        if mode is None or mode["nullable"]:
            await conn.execute(text(f"""
                UPDATE {TABLE}
                SET source_ref_mode = CASE
                    WHEN JSON_TYPE(JSON_EXTRACT(inputs_json, '$.source_ref')) = 'STRING'
                     AND TRIM(JSON_UNQUOTE(JSON_EXTRACT(inputs_json, '$.source_ref'))) <> ''
                    THEN 'manual'
                    ELSE 'branch'
                END
                WHERE source_ref_mode IS NULL
            """))
            await conn.execute(
                text(f"ALTER TABLE {TABLE} MODIFY COLUMN source_ref_mode VARCHAR(16) NOT NULL DEFAULT 'branch'")
            )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
