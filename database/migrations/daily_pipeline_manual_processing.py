"""Add human execution provenance and split untouched entries into classification."""
from infrastructure.db.base import engine
from sqlalchemy import inspect, text


async def run():
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda sync: {c["name"] for c in inspect(sync).get_columns("daily_pipeline_process_node_executions")})
        if "source" not in columns:
            await conn.execute(text("ALTER TABLE daily_pipeline_process_node_executions ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'auto'"))
        if "operator" not in columns:
            await conn.execute(text("ALTER TABLE daily_pipeline_process_node_executions ADD COLUMN operator VARCHAR(255) NULL"))
        # Preserve all started or historical flows. Only an untouched entry changes.
        await conn.execute(text("""
            UPDATE daily_pipeline_process_nodes n
            JOIN daily_pipeline_failures f ON f.id = n.failure_id
            LEFT JOIN daily_pipeline_process_node_executions e ON e.node_id = n.id
            LEFT JOIN daily_pipeline_process_nodes child ON child.previous_node_id = n.id
            SET n.stage_code = 'classify'
            WHERE n.stage_code = 'locating' AND n.status = 'pending'
              AND n.previous_node_id IS NULL AND n.started_at IS NULL
              AND f.processing_status IN ('pending', 'running', 'unresolved')
              AND f.problem_category IS NULL AND e.id IS NULL AND child.id IS NULL
        """))
