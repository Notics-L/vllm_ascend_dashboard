"""Keep retryable failed process nodes active instead of ending their flows."""
from infrastructure.db.base import engine
from sqlalchemy import text


async def run():
    async with engine.begin() as conn:
        await conn.execute(text("""
            UPDATE daily_pipeline_failures f
            SET f.processing_status = 'running', f.ended_at = NULL
            WHERE f.processing_status = 'unresolved' AND f.end_reason IS NULL
              AND EXISTS (
                SELECT 1 FROM daily_pipeline_process_nodes n
                LEFT JOIN daily_pipeline_process_nodes p ON p.id = n.previous_node_id
                WHERE n.failure_id = f.id AND n.status = 'failed'
                  AND (n.previous_node_id IS NULL OR p.status = 'completed')
              )
        """))
