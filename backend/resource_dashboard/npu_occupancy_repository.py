"""Durable raw-env store and all-or-nothing hourly synchronisation."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from infrastructure.db.base import SessionLocal
from resource_dashboard.npu_occupancy_client import NpuOccupancyClient
from resource_dashboard.npu_occupancy_service import parse_timestamp


class NpuOccupancyRepository:
    async def sync(self, start: datetime, end: datetime) -> dict:
        task_id = uuid.uuid4().hex
        async with SessionLocal() as db:
            state = await db.execute(text("SELECT status FROM npu_occupancy_sync_state WHERE id=1 FOR UPDATE"))
            if state.scalar_one_or_none() == "running":
                return {"status": "running"}
            await db.execute(text("UPDATE npu_occupancy_sync_state SET status='running', active_task_id=:task, error_message=NULL, updated_at=UTC_TIMESTAMP() WHERE id=1"), {"task": task_id})
            await db.commit()
        try:
            envs = await asyncio.to_thread(NpuOccupancyClient().fetch_window, start, end)
            snapshot_at = datetime.now(UTC)
            async with SessionLocal() as db:
                for env in envs:
                    env_id = str(env.get("env_id") or "")
                    if not env_id:
                        continue
                    revision = parse_timestamp(env.get("updated_at") or env.get("expires_at") or env.get("created_at")) or snapshot_at
                    await db.execute(text("""
                        INSERT INTO npu_occupancy_raw_envs (env_id, source_updated_at, payload, created_at, updated_at)
                        VALUES (:env_id, :revision, CAST(:payload AS JSON), UTC_TIMESTAMP(), UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=UTC_TIMESTAMP()
                    """), {"env_id": env_id, "revision": revision, "payload": json.dumps(env, default=str)})
                await db.execute(text("""
                    UPDATE npu_occupancy_sync_state
                    SET status='ready', last_successful_sync=UTC_TIMESTAMP(), last_snapshot_at=:snapshot,
                        active_task_id=NULL, error_message=NULL, updated_at=UTC_TIMESTAMP()
                    WHERE id=1 AND active_task_id=:task
                """), {"snapshot": snapshot_at, "task": task_id})
                await db.commit()
            return {"status": "ready", "count": len(envs), "snapshot_at": snapshot_at}
        except Exception as exc:
            async with SessionLocal() as db:
                await db.execute(text("""
                    UPDATE npu_occupancy_sync_state SET status='failed', active_task_id=NULL,
                    error_message=:error, updated_at=UTC_TIMESTAMP() WHERE id=1 AND active_task_id=:task
                """), {"error": str(exc)[:1000], "task": task_id})
                await db.commit()
            raise

    async def load_envs(self) -> tuple[list[dict], datetime | None]:
        async with SessionLocal() as db:
            state = (await db.execute(text("SELECT last_snapshot_at FROM npu_occupancy_sync_state WHERE id=1"))).mappings().first()
            rows = (await db.execute(text("""
                SELECT current.payload
                FROM npu_occupancy_raw_envs AS current
                INNER JOIN (
                    SELECT env_id, MAX(source_updated_at) AS source_updated_at
                    FROM npu_occupancy_raw_envs
                    GROUP BY env_id
                ) AS latest
                    ON latest.env_id = current.env_id
                   AND latest.source_updated_at = current.source_updated_at
            """))).mappings().all()
        envs = [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"] for row in rows]
        snapshot_at = state["last_snapshot_at"] if state else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=UTC)
        return envs, snapshot_at
