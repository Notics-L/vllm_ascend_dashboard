"""上游测试 API。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from upstream_test.service import UpstreamTestService

router = APIRouter()


@router.get("/overview")
async def get_overview(db: DbSession, _: CurrentUser) -> dict[str, Any]:
    """本周 CI 执行情况、最新版本执行情况、issue 概况。"""
    return await UpstreamTestService(db).get_overview()


@router.get("/cases")
async def list_cases(
    db: DbSession,
    _: CurrentUser,
    scope: str = Query(default="weekly", pattern="^(weekly|version)$"),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """用例明细。scope=weekly 为本周用例，scope=version 为最新版本用例。"""
    return await UpstreamTestService(db).list_cases(
        scope=scope,
        status=status,
        category=category,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/issues")
async def list_issues(
    db: DbSession,
    _: CurrentUser,
    state: str = Query(default="open", pattern="^(open|resolved|other)$"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """上游用例关联的 issue。"""
    return await UpstreamTestService(db).list_issues(
        state=state,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/version-detail")
async def get_version_detail(
    db: DbSession,
    _: CurrentUser,
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """最新版本新增用例详情 + 未通过用例关联的 issue。"""
    return await UpstreamTestService(db).get_version_detail(
        status=status, keyword=keyword, page=page, page_size=page_size
    )


@router.get("/ci-detail")
async def get_ci_detail(
    db: DbSession,
    _: CurrentUser,
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """本周 CI 详情：用例明细 + 失败用例关联的 issue。"""
    return await UpstreamTestService(db).get_ci_detail(
        status=status, keyword=keyword, page=page, page_size=page_size
    )


@router.post("/sync")
async def trigger_sync(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    """手动触发一次同步（管理员）。"""
    service = UpstreamTestService(db)
    result = await service.sync()
    # 同步成功后裁剪历史，避免每日快照无限增长
    if result.get("success"):
        removed = await service.prune_history(keep=30)
        result["pruned"] = removed
    return result
