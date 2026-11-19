"""Notify the Daily scheduler after a schedule configuration is committed."""
from datetime import timedelta

import httpx

from infrastructure.core.config import settings
from infrastructure.core.security import create_access_token

SERVICE_SUBJECT = "daily-pipeline-api"
RELOAD_PURPOSE = "daily-schedule-reload"


class SchedulerUpdateError(Exception):
    pass


async def update_schedule(task_key: str) -> None:
    token = create_access_token(
        {"sub": SERVICE_SUBJECT, "purpose": RELOAD_PURPOSE},
        expires_delta=timedelta(seconds=30),
        token_type="service",
    )
    url = f"{settings.DAILY_PIPELINE_SCHEDULER_URL.rstrip('/')}/internal/schedules/{task_key}/reload"
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("taskKey") != task_key or not isinstance(result.get("enabled"), bool):
                raise ValueError("Invalid scheduler acknowledgement")
    except (httpx.HTTPError, ValueError) as exc:
        raise SchedulerUpdateError(
            "配置已保存，但调度任务更新失败，请恢复调度服务后重新保存"
        ) from exc


COLLECTION_PURPOSE = "daily-collection"


async def start_collection(run_id: int) -> None:
    token = create_access_token(
        {"sub": SERVICE_SUBJECT, "purpose": COLLECTION_PURPOSE},
        expires_delta=timedelta(seconds=30), token_type="service",
    )
    url = f"{settings.DAILY_PIPELINE_SCHEDULER_URL.rstrip('/')}/internal/collections/{run_id}/collect"
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            result = response.json()
            if result != {"runId": run_id, "queued": True}:
                raise ValueError("Invalid collection acknowledgement")
    except (httpx.HTTPError, ValueError) as exc:
        raise SchedulerUpdateError("采集请求未送达调度服务，请稍后重试") from exc


PROCESSING_PURPOSE = "daily-processing"


async def start_processing(node_id: int) -> None:
    token = create_access_token(
        {"sub": SERVICE_SUBJECT, "purpose": PROCESSING_PURPOSE},
        expires_delta=timedelta(seconds=30), token_type="service",
    )
    url = f"{settings.DAILY_PIPELINE_SCHEDULER_URL.rstrip('/')}/internal/processing/{node_id}/run"
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            if response.json() != {"nodeId": node_id, "queued": True}:
                raise ValueError("Invalid processing acknowledgement")
    except (httpx.HTTPError, ValueError) as exc:
        raise SchedulerUpdateError("自动处理任务未送达调度服务，请稍后重试") from exc
