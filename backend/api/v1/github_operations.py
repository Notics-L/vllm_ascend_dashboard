"""Read-only access to the GitHub operation history."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import func, select

from api.deps import CurrentAdminUser, DbSession
from infrastructure.core.github_audit import CredentialSource
from infrastructure.persistence.models import GitHubOperation

router = APIRouter(prefix="/github-operations", tags=["GitHub 操作记录"])

OperationResult = Literal["success", "redirect", "failed"]
OperationType = Literal["read", "write"]


class OperationItem(BaseModel):
    id: str
    occurred_at: datetime
    process: str
    source: str
    credential_source: CredentialSource | None
    actor: str | None
    repository: str | None
    method: str
    operation_type: OperationType
    endpoint: str
    result: OperationResult
    http_status: int | None
    duration_ms: int
    error_type: str | None
    github_request_id: str | None


class OperationPage(BaseModel):
    items: list[OperationItem]
    total: int


@router.get("", response_model=OperationPage)
async def list_github_operations(
    db: DbSession,
    _admin: CurrentAdminUser,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    repository: Annotated[str | None, Query(min_length=1, max_length=140)] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
    actor: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    credential_source: CredentialSource | None = None,
    operation_type: OperationType | None = None,
    result: OperationResult | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
) -> OperationPage:
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="开始时间必须早于结束时间")

    table = GitHubOperation.__table__
    columns = table.c
    filters = []
    if repository is not None:
        filters.append(columns.repository == repository)
    if source is not None:
        filters.append(columns.source == source)
    if actor is not None:
        filters.append(columns.actor == actor)
    if credential_source is not None:
        filters.append(columns.credential_source == credential_source)
    if operation_type is not None:
        filters.append(columns.operation_type == operation_type)
    if result is not None:
        filters.append(columns.result == result)
    if start is not None:
        filters.append(columns.occurred_at >= start.astimezone(UTC).replace(tzinfo=None))
    if end is not None:
        filters.append(columns.occurred_at < end.astimezone(UTC).replace(tzinfo=None))

    total = await db.scalar(select(func.count()).select_from(table).where(*filters))
    rows = await db.execute(
        select(table)
        .where(*filters)
        .order_by(columns.occurred_at.desc(), columns.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = []
    for row in rows.mappings():
        item = dict(row)
        item["id"] = str(item["id"])
        item["occurred_at"] = item["occurred_at"].replace(tzinfo=UTC)
        items.append(OperationItem.model_validate(item))
    return OperationPage(items=items, total=total or 0)
