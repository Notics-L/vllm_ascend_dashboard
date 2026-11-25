"""上游测试服务：采集落库与页面查询。"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.config import settings
from upstream_test.collector import UpstreamTestCollector
from infrastructure.persistence.models import (
    UpstreamTestCaseResult,
    UpstreamTestIssue,
    UpstreamTestSnapshot,
)

logger = logging.getLogger(__name__)

# 「未通过」用例的状态集合：skipped 必须包含在内。
# 上游把"无法执行"的用例标成 skipped（例如「无法执行-后端」），只看 failed
# 会严重低估问题规模。首页统计与详情页列表都引用这一处，避免数字对不上。
NOT_PASSED_STATUSES = ("failed", "unknown", "skipped")

# 页面默认认为"数据过期"的阈值：每天一次同步，超过 36 小时没有成功快照就提示
STALE_AFTER = timedelta(hours=36)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class UpstreamTestService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------------------------------------------------------- 同步

    async def sync(self) -> dict[str, Any]:
        """抓取上游仓库并写入一份新快照。失败也记录，便于页面提示。"""
        collector = UpstreamTestCollector()
        try:
            payload = await collector.collect(
                ci_token=getattr(settings, "GITHUB_TOKEN", "") or "",
                ci_owner=getattr(settings, "UPSTREAM_TEST_CI_OWNER", "vllm-project"),
                ci_repo=getattr(settings, "UPSTREAM_TEST_CI_REPO", "vllm-ascend"),
                ci_workflow_id=getattr(settings, "UPSTREAM_TEST_CI_WORKFLOW_ID", None) or None,
            )
        except Exception as exc:  # noqa: BLE001 - 记录失败快照而非静默
            logger.error("Upstream test sync failed: %s", exc, exc_info=True)
            snapshot = UpstreamTestSnapshot(
                fetched_at=datetime.now(UTC),
                source_repo="vllm-ascend/upstream-test",
                status="failed",
                error_message=str(exc)[:4000],
            )
            self.db.add(snapshot)
            await self.db.commit()
            return {"success": False, "error": str(exc), "snapshot_id": snapshot.id}

        snapshot = UpstreamTestSnapshot(
            fetched_at=payload["fetched_at"],
            source_repo=payload["source_repo"],
            status="success",
            weekly_file=payload["weekly_file"],
            weekly_summary=payload["weekly_summary"],
            weekly_failure_reasons=payload.get("weekly_failure_reasons"),
            version_name=payload["version_name"],
            version_file=payload["version_file"],
            previous_version_name=payload.get("previous_version_name"),
            version_summary=payload["version_summary"],
            version_failure_reasons=payload.get("version_failure_reasons"),
            issue_summary=payload["issue_summary"],
            issue_added_count=payload.get("issue_added_count"),
            upstream_tests=payload.get("upstream_tests"),
            ci_summary=payload["ci_summary"],
            weekly_cases=payload["weekly_cases"],
            version_cases=payload["version_cases"],
            issues=payload["issues"],
            warnings=payload.get("warnings") or None,
        )
        self.db.add(snapshot)
        await self.db.flush()

        # 明细展开到独立表，便于按用例/原因分类聚合查询
        for row in payload["weekly_cases"]:
            self.db.add(
                UpstreamTestCaseResult(
                    snapshot_id=snapshot.id,
                    scope="weekly",
                    version_name=payload["version_name"],
                    test_path=row["test_path"][:500],
                    status=row["status"],
                    snippet=row["snippet"],
                    analysis=row["analysis"],
                    category=(row["category"] or None),
                    case_version=row["case_version"],
                    removed=row["removed"],
                )
            )
        for row in payload["version_cases"]:
            self.db.add(
                UpstreamTestCaseResult(
                    snapshot_id=snapshot.id,
                    scope="version",
                    version_name=payload["version_name"],
                    test_path=row["test_path"][:500],
                    status=row["status"],
                    snippet=row["snippet"],
                    analysis=row["analysis"],
                    category=(row["category"] or None),
                    case_version=row["case_version"],
                    removed=row["removed"],
                )
            )
        for row in payload["issues"]:
            self.db.add(
                UpstreamTestIssue(
                    snapshot_id=snapshot.id,
                    state=row["state"],
                    issue_number=row.get("issue_number"),
                    issue_url=row.get("issue_url"),
                    issue_title=(row.get("issue_title") or None),
                    test_path=(row.get("test_path") or None),
                    impact_level=row.get("impact_level"),
                    impact=row.get("impact"),
                    notes=row.get("notes"),
                )
            )

        await self.db.commit()
        logger.info(
            "Upstream test sync stored snapshot %s: weekly=%s version=%s issues=%s",
            snapshot.id,
            payload["weekly_summary"],
            payload["version_name"],
            payload["issue_summary"],
        )
        return {
            "success": True,
            "snapshot_id": snapshot.id,
            "weekly_summary": payload["weekly_summary"],
            "version_name": payload["version_name"],
            "version_summary": payload["version_summary"],
            "issue_summary": payload["issue_summary"],
            "warnings": payload.get("warnings") or [],
        }

    # ---------------------------------------------------------------- 查询

    async def _latest_snapshot(self) -> UpstreamTestSnapshot | None:
        stmt = (
            select(UpstreamTestSnapshot)
            .where(UpstreamTestSnapshot.status == "success")
            .order_by(UpstreamTestSnapshot.fetched_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_overview(self) -> dict[str, Any]:
        """页面主视图：新增用例、本周 CI、全量 issue 三个板块。"""
        snapshot = await self._latest_snapshot()
        if snapshot is None:
            latest_any = (
                await self.db.execute(
                    select(UpstreamTestSnapshot)
                    .order_by(UpstreamTestSnapshot.fetched_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return {
                "available": False,
                "has_snapshot": latest_any is not None,
                "last_error": getattr(latest_any, "error_message", None),
                "message": "尚未同步上游测试结果",
            }

        fetched = _aware(snapshot.fetched_at)
        stale = bool(fetched and datetime.now(UTC) - fetched > STALE_AFTER)

        # 原因分类分布（本周失败/未知用例）
        category_rows = (
            await self.db.execute(
                select(
                    UpstreamTestCaseResult.category,
                    func.count(UpstreamTestCaseResult.id),
                )
                .where(
                    UpstreamTestCaseResult.snapshot_id == snapshot.id,
                    UpstreamTestCaseResult.scope == "weekly",
                    UpstreamTestCaseResult.category.is_not(None),
                )
                .group_by(UpstreamTestCaseResult.category)
                .order_by(func.count(UpstreamTestCaseResult.id).desc())
                .limit(12)
            )
        ).all()

        return {
            "available": True,
            "snapshot_id": snapshot.id,
            "fetched_at": fetched.isoformat() if fetched else None,
            "stale": stale,
            "source_repo": snapshot.source_repo,
            "warnings": snapshot.warnings or [],
            "weekly": {
                "file": snapshot.weekly_file,
                "summary": snapshot.weekly_summary or {},
                "categories": [
                    {"category": row[0], "count": int(row[1])} for row in category_rows
                ],
                "failure_reasons": snapshot.weekly_failure_reasons or [],
                # 本周失败用例关联的开启中 issue 数（与 CI 详情页那块口径一致）
                "issue_count": await self._count_issues_for_failed_cases(
                    snapshot.id, "weekly"
                ),
            },
            "version": {
                "name": snapshot.version_name,
                "file": snapshot.version_file,
                "previous_name": snapshot.previous_version_name,
                "summary": snapshot.version_summary or {},
                "failure_reasons": snapshot.version_failure_reasons or [],
            },
            "issues": {
                "summary": snapshot.issue_summary or {},
                "added_count": snapshot.issue_added_count,
            },
            "upstream_tests": snapshot.upstream_tests or {"available": False},
            "ci": snapshot.ci_summary or {"available": False},
        }

    async def list_cases(
        self,
        *,
        scope: str = "weekly",
        status: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        snapshot = await self._latest_snapshot()
        if snapshot is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

        conditions = [
            UpstreamTestCaseResult.snapshot_id == snapshot.id,
            UpstreamTestCaseResult.scope == scope,
        ]
        if status:
            conditions.append(UpstreamTestCaseResult.status == status)
        if category:
            conditions.append(UpstreamTestCaseResult.category == category)
        if keyword:
            conditions.append(UpstreamTestCaseResult.test_path.like(f"%{keyword}%"))

        total = int(
            (
                await self.db.execute(
                    select(func.count(UpstreamTestCaseResult.id)).where(*conditions)
                )
            ).scalar_one()
        )

        rows = (
            await self.db.execute(
                select(UpstreamTestCaseResult)
                .where(*conditions)
                .order_by(UpstreamTestCaseResult.test_path.asc())
                .offset(max(0, (page - 1) * page_size))
                .limit(page_size)
            )
        ).scalars().all()

        return {
            "items": [
                {
                    "id": row.id,
                    "test_path": row.test_path,
                    "status": row.status,
                    "snippet": row.snippet,
                    "analysis": row.analysis,
                    "category": row.category,
                    "case_version": row.case_version,
                    "removed": row.removed,
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def list_issues(
        self,
        *,
        state: str = "open",
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        snapshot = await self._latest_snapshot()
        if snapshot is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

        conditions = [
            UpstreamTestIssue.snapshot_id == snapshot.id,
            UpstreamTestIssue.state == state,
        ]
        if keyword:
            conditions.append(UpstreamTestIssue.issue_title.like(f"%{keyword}%"))

        total = int(
            (
                await self.db.execute(select(func.count(UpstreamTestIssue.id)).where(*conditions))
            ).scalar_one()
        )
        rows = (
            await self.db.execute(
                select(UpstreamTestIssue)
                .where(*conditions)
                .order_by(UpstreamTestIssue.issue_number.desc())
                .offset(max(0, (page - 1) * page_size))
                .limit(page_size)
            )
        ).scalars().all()

        return {
            "items": [
                {
                    "id": row.id,
                    "issue_number": row.issue_number,
                    "issue_url": row.issue_url,
                    "issue_title": row.issue_title,
                    "test_path": row.test_path,
                    "impact_level": row.impact_level,
                    "impact": row.impact,
                    "notes": row.notes,
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ---------------------------------------------------------------- 详情

    async def get_version_detail(
        self,
        *,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """「新增用例」详情：最新版本用例表 + 与未通过用例关联的 issue。"""
        snapshot = await self._latest_snapshot()
        if snapshot is None:
            return {
                "cases": {"items": [], "total": 0, "page": page, "page_size": page_size},
                "issues": [],
                "version": None,
            }

        cases = await self.list_cases(
            scope="version", status=status, keyword=keyword, page=page, page_size=page_size
        )
        failed_paths = (
            await self.db.execute(
                select(UpstreamTestCaseResult.test_path)
                .where(
                    UpstreamTestCaseResult.snapshot_id == snapshot.id,
                    UpstreamTestCaseResult.scope == "version",
                    UpstreamTestCaseResult.status.in_(NOT_PASSED_STATUSES),
                )
                .distinct()
            )
        ).scalars().all()

        issues = await self._issues_for_paths(snapshot.id, list(failed_paths))
        return {
            "cases": cases,
            "issues": issues,
            "version": {
                "name": snapshot.version_name,
                "previous_name": snapshot.previous_version_name,
                "summary": snapshot.version_summary or {},
                "failure_reasons": snapshot.version_failure_reasons or [],
            },
        }

    async def get_ci_detail(
        self,
        *,
        status: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """「本周 CI」详情：本周用例表 + 失败用例关联的 issue。"""
        snapshot = await self._latest_snapshot()
        if snapshot is None:
            return {
                "cases": {"items": [], "total": 0, "page": page, "page_size": page_size},
                "issues": [],
                "ci": {"available": False},
            }

        cases = await self.list_cases(
            scope="weekly", status=status, keyword=keyword, page=page, page_size=page_size
        )
        failed_paths = (
            await self.db.execute(
                select(UpstreamTestCaseResult.test_path)
                .where(
                    UpstreamTestCaseResult.snapshot_id == snapshot.id,
                    UpstreamTestCaseResult.scope == "weekly",
                    UpstreamTestCaseResult.status.in_(NOT_PASSED_STATUSES),
                )
                .distinct()
            )
        ).scalars().all()

        issues = await self._issues_for_paths(snapshot.id, list(failed_paths))
        return {
            "cases": cases,
            "issues": issues,
            "ci": snapshot.ci_summary or {"available": False},
            "weekly": {
                "file": snapshot.weekly_file,
                "summary": snapshot.weekly_summary or {},
                "failure_reasons": snapshot.weekly_failure_reasons or [],
            },
        }

    async def _count_issues_for_failed_cases(
        self, snapshot_id: int, scope: str
    ) -> int:
        """统计某 scope 下未通过用例关联的开启中 issue 去重数量。

        与 CI 详情页「失败用例关联的 issue」同一口径，避免首页数字和详情页对不上。
        同一 issue 关联多个用例时只算一个。
        """
        stmt = (
            select(func.count(func.distinct(UpstreamTestIssue.issue_number)))
            .select_from(UpstreamTestIssue)
            .join(
                UpstreamTestCaseResult,
                (UpstreamTestCaseResult.snapshot_id == UpstreamTestIssue.snapshot_id)
                & (UpstreamTestCaseResult.test_path == UpstreamTestIssue.test_path),
            )
            .where(
                UpstreamTestIssue.snapshot_id == snapshot_id,
                UpstreamTestIssue.state == "open",
                UpstreamTestCaseResult.scope == scope,
                UpstreamTestCaseResult.status.in_(NOT_PASSED_STATUSES),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one() or 0)

    async def _issues_for_paths(
        self, snapshot_id: int, test_paths: list[str]
    ) -> list[dict[str, Any]]:
        """取出与给定用例路径关联的未关闭 issue。"""
        if not test_paths:
            return []
        rows = (
            await self.db.execute(
                select(UpstreamTestIssue)
                .where(
                    UpstreamTestIssue.snapshot_id == snapshot_id,
                    UpstreamTestIssue.state == "open",
                    UpstreamTestIssue.test_path.in_(test_paths),
                )
                .order_by(UpstreamTestIssue.issue_number.desc())
            )
        ).scalars().all()
        return [
            {
                "id": row.id,
                "issue_number": row.issue_number,
                "issue_url": row.issue_url,
                "issue_title": row.issue_title,
                "test_path": row.test_path,
                "impact_level": row.impact_level,
                "impact": row.impact,
                "notes": row.notes,
            }
            for row in rows
        ]

    async def prune_history(self, keep: int = 30) -> int:
        """只保留最近 N 份快照，避免每日同步无限膨胀。"""
        ids = (
            await self.db.execute(
                select(UpstreamTestSnapshot.id)
                .order_by(UpstreamTestSnapshot.fetched_at.desc())
                .offset(keep)
            )
        ).scalars().all()
        if not ids:
            return 0
        await self.db.execute(
            delete(UpstreamTestIssue).where(UpstreamTestIssue.snapshot_id.in_(ids))
        )
        await self.db.execute(
            delete(UpstreamTestCaseResult).where(
                UpstreamTestCaseResult.snapshot_id.in_(ids)
            )
        )
        await self.db.execute(delete(UpstreamTestSnapshot).where(UpstreamTestSnapshot.id.in_(ids)))
        await self.db.commit()
        return len(ids)
