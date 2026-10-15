from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from api.deps import CurrentUser
from contracts.schemas.npu_occupancy import (
    NpuOccupancyAnalysisResponse,
    NpuOccupancyDetailsResponse,
    NpuOccupancyOptionsResponse,
    NpuOccupancyTrendResponse,
)
from infrastructure.core.config import settings
from resource_dashboard.npu_occupancy_client import NpuOccupancyUpstreamError
from resource_dashboard.npu_occupancy_repository import NpuOccupancyRepository
from resource_dashboard.npu_occupancy_service import NpuOccupancyService, parse_names

router = APIRouter()


def _validate_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="start and end must include a timezone")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if (end - start).total_seconds() > settings.NPU_OCCUPANCY_MAX_WINDOW_SECONDS:
        raise HTTPException(status_code=422, detail="requested window exceeds the configured maximum")
    return start, end


def _upstream_error(exc: NpuOccupancyUpstreamError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.post("/refresh")
async def refresh_snapshot(
    current_user: CurrentUser,
    start: datetime = Query(...),
    end: datetime = Query(...),
):
    """Explicit user-triggered upstream refresh; normal reads stay local."""
    start, end = _validate_window(start, end)
    try:
        return await NpuOccupancyRepository().sync(start, end)
    except NpuOccupancyUpstreamError as exc:
        raise _upstream_error(exc) from exc


@router.get("/options", response_model=NpuOccupancyOptionsResponse)
async def get_options(dimension: Literal["pool", "project"], current_user: CurrentUser):
    return {"dimension": dimension, "options": NpuOccupancyService().options(dimension)}


@router.get("/trend", response_model=NpuOccupancyTrendResponse)
async def get_trend(
    current_user: CurrentUser,
    dimension: Literal["pool", "project"],
    names: str | None = None,
    start: datetime = Query(...),
    end: datetime = Query(...),
):
    start, end = _validate_window(start, end)
    records, snapshot_at = await NpuOccupancyRepository().load_records()
    return NpuOccupancyService(records=records, snapshot_at=snapshot_at).trend(dimension=dimension, names=parse_names(names), start=start, end=end)


@router.get("/details", response_model=NpuOccupancyDetailsResponse)
async def get_details(
    current_user: CurrentUser,
    dimension: Literal["pool", "project"],
    timestamp: datetime = Query(...),
    group_by: Literal["pool", "project"] = "project",
    names: str | None = None,
    start: datetime = Query(...),
    end: datetime = Query(...),
):
    start, end = _validate_window(start, end)
    if timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail="timestamp must include a timezone")
    timestamp = timestamp.astimezone(UTC)
    if not start <= timestamp <= end:
        raise HTTPException(status_code=422, detail="timestamp must be within start and end")
    records, snapshot_at = await NpuOccupancyRepository().load_records()
    return NpuOccupancyService(records=records, snapshot_at=snapshot_at).details(dimension=dimension, names=parse_names(names), timestamp=timestamp, group_by=group_by, start=start, end=end)


@router.get("/analysis", response_model=NpuOccupancyAnalysisResponse)
async def get_analysis(
    current_user: CurrentUser,
    dimension: Literal["pool", "project"],
    names: str | None = None,
    start: datetime = Query(...),
    end: datetime = Query(...),
):
    """Range analysis; the curve drawer remains on the single-time details API."""
    start, end = _validate_window(start, end)
    records, snapshot_at = await NpuOccupancyRepository().load_records()
    return NpuOccupancyService(records=records, snapshot_at=snapshot_at).analysis(
        dimension=dimension, names=parse_names(names), start=start, end=end,
    )
