"""代码度量看板 API"""
import csv
import io
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from infrastructure.persistence.models import (
    CodeComplexityDetail,
    CodeDuplicationDetail,
    CodeMetricsFileHeatmap,
    CodeMetricsSnapshot,
    CodeSecurityDetail,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _calculate_health_score(data: dict) -> dict:
    """计算六维健康度评分"""
    total_loc = max(data.get("total_loc", 1), 1)
    kloc = total_loc / 1000
    total_functions = max(data.get("total_functions", 0), 1)

    cc_adequacy = data.get("cc_adequacy", 0) or (
        (total_functions - data.get("cc_huge_count", 0)) / total_functions * 100
    )
    score_complexity = max(0, min(100, cc_adequacy))

    security_kloc = (
        data.get("unsafe_functions_count", 0) + data.get("warning_suppression_count", 0)
    ) / kloc
    score_security = max(0, 100 - security_kloc * 10)

    score_duplication = max(0, 100 - data.get("dup_ratio", 0))

    score_method_size = max(0, 100 - data.get("huge_method_ratio", 0))

    debt_kloc = (
        data.get("todo_count", 0)
        + data.get("fixme_count", 0)
        + data.get("hack_count", 0)
    ) / kloc
    score_tech_debt = max(0, 100 - debt_kloc * 5)

    lint_kloc = data.get("lint_errors", 0) / kloc
    score_lint = max(0, 100 - lint_kloc * 5)

    total = (
        score_complexity * 0.20
        + score_security * 0.20
        + score_duplication * 0.15
        + score_method_size * 0.15
        + score_tech_debt * 0.15
        + score_lint * 0.15
    )

    return {
        "health_score": round(total, 1),
        "health_score_complexity": round(score_complexity, 1),
        "health_score_security": round(score_security, 1),
        "health_score_duplication": round(score_duplication, 1),
        "health_score_method_size": round(score_method_size, 1),
        "health_score_tech_debt": round(score_tech_debt, 1),
        "health_score_lint": round(score_lint, 1),
    }


async def _sync_heatmap_from_github(
    db,
    github_client,
    owner: str,
    repo: str,
    days: int = 30,
) -> dict:
    """Shared helper: aggregate file change frequency from PRs via GitHub API.

    Fetches PR file lists from the GitHub API (not from stored PR.data) and
    upserts into the ``code_metrics_file_heatmap`` table.
    """
    from collections import Counter

    from infrastructure.persistence.models import PullRequest

    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(PullRequest.pr_number, PullRequest.title)
        .where(
            PullRequest.owner == owner,
            PullRequest.repo == repo,
            PullRequest.created_at >= cutoff,
        )
        .order_by(PullRequest.created_at.desc())
        .limit(100)
    )
    result = await db.execute(stmt)

    file_changes: Counter = Counter()
    file_bug_fixes: Counter = Counter()
    bug_keywords = ["fix", "bug", "error", "crash", "fail", "issue", "patch"]

    for row in result:
        pr_number = row[0]
        title = (row[1] or "").lower()
        is_bug_fix = any(kw in title for kw in bug_keywords)
        try:
            files = await github_client.get_pr_files(owner, repo, pr_number)
            for f in files:
                path = f.get("filename", "") if isinstance(f, dict) else ""
                if path:
                    file_changes[path] += 1
                    if is_bug_fix:
                        file_bug_fixes[path] += 1
        except Exception as e:
            logger.warning(f"Failed to fetch files for PR #{pr_number}: {e}")

    updated = 0
    for path, count in file_changes.most_common(500):
        existing = await db.execute(
            select(CodeMetricsFileHeatmap).where(
                CodeMetricsFileHeatmap.repo == "vllm-ascend",
                CodeMetricsFileHeatmap.file_path == path,
            )
        )
        record = existing.scalar_one_or_none()
        if record:
            record.change_count = count
            record.bug_fix_count = file_bug_fixes.get(path, 0)
            record.last_changed = datetime.now(UTC)
        else:
            db.add(
                CodeMetricsFileHeatmap(
                    repo="vllm-ascend",
                    file_path=path,
                    change_count=count,
                    bug_fix_count=file_bug_fixes.get(path, 0),
                    last_changed=datetime.now(UTC),
                )
            )
        updated += 1

    await db.commit()
    return {"updated": updated, "total_files": len(file_changes)}


@router.post("/snapshot", summary="上传代码度量快照（CI 调用）")
async def upload_snapshot(
    body: dict,
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """接收 CI 上传的代码度量快照数据"""
    snapshot_date = body.get("snapshot_date")
    if not snapshot_date:
        snapshot_date = date.today().isoformat()

    repo = body.get("repo", "vllm-ascend")
    branch = body.get("branch", "main")

    # 幂等：同日期同repo同branch先删旧数据
    old = await db.execute(
        select(CodeMetricsSnapshot).where(
            CodeMetricsSnapshot.snapshot_date == snapshot_date,
            CodeMetricsSnapshot.repo == repo,
            CodeMetricsSnapshot.branch == branch,
        )
    )
    for old_snap in old.scalars().all():
        await db.execute(
            delete(CodeComplexityDetail).where(
                CodeComplexityDetail.snapshot_id == old_snap.id
            )
        )
        await db.execute(
            delete(CodeDuplicationDetail).where(
                CodeDuplicationDetail.snapshot_id == old_snap.id
            )
        )
        await db.execute(
            delete(CodeSecurityDetail).where(
                CodeSecurityDetail.snapshot_id == old_snap.id
            )
        )
        await db.delete(old_snap)
    await db.flush()

    # 计算六维健康度评分（覆盖 body 中的值）
    health = _calculate_health_score(body)

    try:
        # 创建快照
        snapshot = CodeMetricsSnapshot(
            repo=repo,
            branch=branch,
            tag=body.get("tag"),
            snapshot_date=snapshot_date,
            collection_status=body.get("collection_status", "complete"),
            collection_duration_seconds=body.get("collection_duration_seconds", 0),
            total_loc=body.get("total_loc", 0),
            total_raw_lines=body.get("total_raw_lines", 0),
            loc_python=body.get("loc_python", 0),
            loc_cpp=body.get("loc_cpp", 0),
            loc_c=body.get("loc_c", 0),
            loc_cmake=body.get("loc_cmake", 0),
            loc_shell=body.get("loc_shell", 0),
            total_functions=body.get("total_functions", 0),
            total_files=body.get("total_files", 0),
            cc_total=body.get("cc_total", 0),
            cc_per_method=body.get("cc_per_method", 0),
            cc_maximum=body.get("cc_maximum", 0),
            cc_huge_count=body.get("cc_huge_count", 0),
            cc_huge_ratio=body.get("cc_huge_ratio", 0),
            cc_adequacy=body.get("cc_adequacy", 0),
            max_depth=body.get("max_depth", 0),
            depth_huge_count=body.get("depth_huge_count", 0),
            depth_huge_ratio=body.get("depth_huge_ratio", 0),
            method_lines_total=body.get("method_lines_total", 0),
            lines_per_method=body.get("lines_per_method", 0),
            huge_method_count=body.get("huge_method_count", 0),
            huge_method_ratio=body.get("huge_method_ratio", 0),
            huge_file_count=body.get("huge_file_count", 0),
            huge_headerfile_count=body.get("huge_headerfile_count", 0),
            dup_blocks=body.get("dup_blocks", 0),
            dup_lines=body.get("dup_lines", 0),
            dup_ratio=body.get("dup_ratio", 0),
            unsafe_functions_count=body.get("unsafe_functions_count", 0),
            warning_suppression_count=body.get("warning_suppression_count", 0),
            lint_errors=body.get("lint_errors", 0),
            lint_warnings=body.get("lint_warnings", 0),
            todo_count=body.get("todo_count", 0),
            fixme_count=body.get("fixme_count", 0),
            hack_count=body.get("hack_count", 0),
            health_score=health["health_score"],
            health_score_complexity=health["health_score_complexity"],
            health_score_security=health["health_score_security"],
            health_score_duplication=health["health_score_duplication"],
            health_score_method_size=health["health_score_method_size"],
            health_score_tech_debt=health["health_score_tech_debt"],
            health_score_lint=health["health_score_lint"],
            module_loc=body.get("module_loc"),
            language_loc=body.get("language_loc"),
        )
        db.add(snapshot)
        await db.flush()

        # 存储复杂度明细（批量插入）
        db.add_all(
            [
                CodeComplexityDetail(
                    snapshot_id=snapshot.id,
                    file_path=item.get("file_path", ""),
                    function_name=item.get("function_name", ""),
                    language=item.get("language"),
                    cyclomatic_complexity=item.get("cyclomatic_complexity"),
                    max_nesting_depth=item.get("max_nesting_depth"),
                    function_lines=item.get("function_lines"),
                    start_line=item.get("start_line"),
                )
                for item in body.get("complexity_details", [])[:500]
            ]
        )

        # 存储重复率明细（批量插入）
        db.add_all(
            [
                CodeDuplicationDetail(
                    snapshot_id=snapshot.id,
                    file_a=item.get("file_a", ""),
                    file_b=item.get("file_b", ""),
                    lines=item.get("lines", 0),
                    token_count=item.get("token_count", 0),
                    fragment=item.get("fragment"),
                )
                for item in body.get("duplication_details", [])[:200]
            ]
        )

        # 存储安全规范明细（批量插入）
        db.add_all(
            [
                CodeSecurityDetail(
                    snapshot_id=snapshot.id,
                    file_path=item.get("file_path", ""),
                    line_number=item.get("line_number"),
                    severity=item.get("severity"),
                    tool=item.get("tool"),
                    rule_id=item.get("rule_id"),
                    message=item.get("message"),
                )
                for item in body.get("security_details", [])[:500]
            ]
        )

        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"id": snapshot.id, "status": "saved", "snapshot_date": snapshot_date}


@router.get("/overview", summary="总览数据")
async def get_overview(
    current_user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
):
    """获取代码度量总览数据（最新快照）"""
    stmt = select(CodeMetricsSnapshot).order_by(
        CodeMetricsSnapshot.snapshot_date.desc()
    ).limit(1)
    result = await db.execute(stmt)
    snapshot = result.scalar_one_or_none()

    if not snapshot:
        return {"has_data": False, "message": "暂无代码度量数据"}

    return {
        "has_data": True,
        "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot.snapshot_date else None,
        "collection_status": snapshot.collection_status,
        "health_score": snapshot.health_score,
        "health_scores": {
            "complexity": snapshot.health_score_complexity,
            "security": snapshot.health_score_security,
            "duplication": snapshot.health_score_duplication,
            "method_size": snapshot.health_score_method_size,
            "tech_debt": snapshot.health_score_tech_debt,
            "lint": snapshot.health_score_lint,
        },
        "metrics": {
            "total_loc": snapshot.total_loc,
            "total_functions": snapshot.total_functions,
            "total_files": snapshot.total_files,
            "cc_per_method": snapshot.cc_per_method,
            "cc_maximum": snapshot.cc_maximum,
            "cc_huge_count": snapshot.cc_huge_count,
            "dup_ratio": snapshot.dup_ratio,
            "dup_blocks": snapshot.dup_blocks,
            "unsafe_functions_count": snapshot.unsafe_functions_count,
            "lint_errors": snapshot.lint_errors,
            "todo_count": snapshot.todo_count,
            "fixme_count": snapshot.fixme_count,
        },
        "language_loc": snapshot.language_loc or {},
        "module_loc": snapshot.module_loc or {},
    }


@router.get("/complexity", summary="复杂度详情")
async def get_complexity(
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=500),
):
    """获取最新快照的复杂度明细"""
    latest = await db.execute(
        select(CodeMetricsSnapshot.id).order_by(
            CodeMetricsSnapshot.snapshot_date.desc()
        ).limit(1)
    )
    snapshot_id = latest.scalar_one_or_none()
    if not snapshot_id:
        return {"items": []}

    stmt = (
        select(CodeComplexityDetail)
        .where(CodeComplexityDetail.snapshot_id == snapshot_id)
        .order_by(CodeComplexityDetail.cyclomatic_complexity.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return {
        "items": [
            {
                "file_path": r.file_path,
                "function_name": r.function_name,
                "language": r.language,
                "cyclomatic_complexity": r.cyclomatic_complexity,
                "max_nesting_depth": r.max_nesting_depth,
                "function_lines": r.function_lines,
            }
            for r in result.scalars().all()
        ]
    }


@router.get("/duplication", summary="重复率详情")
async def get_duplication(
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=200),
):
    """获取最新快照的重复代码块明细"""
    latest = await db.execute(
        select(CodeMetricsSnapshot.id).order_by(
            CodeMetricsSnapshot.snapshot_date.desc()
        ).limit(1)
    )
    snapshot_id = latest.scalar_one_or_none()
    if not snapshot_id:
        return {"items": []}

    stmt = (
        select(CodeDuplicationDetail)
        .where(CodeDuplicationDetail.snapshot_id == snapshot_id)
        .order_by(CodeDuplicationDetail.lines.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return {
        "items": [
            {
                "file_a": r.file_a,
                "file_b": r.file_b,
                "lines": r.lines,
                "fragment": r.fragment[:200] if r.fragment else None,
            }
            for r in result.scalars().all()
        ]
    }


@router.get("/security", summary="安全规范明细")
async def get_security(
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(50, ge=1, le=500),
):
    """获取最新快照的安全规范明细"""
    latest = await db.execute(
        select(CodeMetricsSnapshot.id)
        .order_by(CodeMetricsSnapshot.snapshot_date.desc())
        .limit(1)
    )
    snapshot_id = latest.scalar_one_or_none()
    if not snapshot_id:
        return {"items": []}
    stmt = (
        select(CodeSecurityDetail)
        .where(CodeSecurityDetail.snapshot_id == snapshot_id)
        .order_by(CodeSecurityDetail.severity.asc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return {
        "items": [
            {
                "file_path": r.file_path,
                "line_number": r.line_number,
                "severity": r.severity,
                "tool": r.tool,
                "rule_id": r.rule_id,
                "message": r.message,
            }
            for r in result.scalars().all()
        ]
    }


@router.get("/heatmap", summary="文件热力图")
async def get_heatmap(
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(20, ge=1, le=100),
):
    """获取文件变更热力图 Top N"""
    stmt = (
        select(CodeMetricsFileHeatmap)
        .order_by(CodeMetricsFileHeatmap.change_count.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return {
        "items": [
            {
                "file_path": r.file_path,
                "change_count": r.change_count,
                "bug_fix_count": r.bug_fix_count,
                "last_changed": r.last_changed.isoformat() if r.last_changed else None,
            }
            for r in result.scalars().all()
        ]
    }


@router.post("/heatmap/sync", summary="同步文件热力图数据")
async def sync_heatmap(
    current_user: CurrentAdminUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
):
    """从 PR 数据聚合文件变更频率，更新热力图"""
    from uuid import uuid4

    from infrastructure.tasks.task_manager import TaskManager

    task_id = await TaskManager.create_task(
        db,
        "code_heatmap_sync",
        {"days": days},
        f"code_heatmap_sync:manual:{uuid4()}",
        required_capability="python",
        priority=10,
    )
    await db.commit()
    return {"status": "queued", "task_id": task_id, "days": days}


@router.get("/trends", summary="趋势数据")
async def get_trends(
    current_user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
):
    """获取代码度量趋势数据"""
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(CodeMetricsSnapshot)
        .where(CodeMetricsSnapshot.snapshot_date >= cutoff)
        .order_by(CodeMetricsSnapshot.snapshot_date.asc())
    )
    result = await db.execute(stmt)
    snapshots = result.scalars().all()
    return {
        "items": [
            {
                "date": s.snapshot_date.isoformat() if s.snapshot_date else None,
                "total_loc": s.total_loc,
                "total_functions": s.total_functions,
                "cc_per_method": s.cc_per_method,
                "cc_huge_count": s.cc_huge_count,
                "dup_ratio": s.dup_ratio,
                "health_score": s.health_score,
                "lint_errors": s.lint_errors,
                "todo_count": s.todo_count + s.fixme_count + s.hack_count,
            }
            for s in snapshots
        ]
    }


@router.get("/export", summary="导出度量数据")
async def export_metrics(
    current_user: CurrentUser,
    db: DbSession,
    format: str = Query("json", pattern="^(json|csv)$"),
    days: int = Query(30, ge=1, le=365),
):
    """导出代码度量数据"""
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(CodeMetricsSnapshot)
        .where(CodeMetricsSnapshot.snapshot_date >= cutoff)
        .order_by(CodeMetricsSnapshot.snapshot_date.asc())
    )
    result = await db.execute(stmt)
    snapshots = result.scalars().all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "date",
                "total_loc",
                "total_functions",
                "cc_per_method",
                "cc_huge_count",
                "dup_ratio",
                "health_score",
                "lint_errors",
                "todo_count",
            ]
        )
        for s in snapshots:
            writer.writerow(
                [
                    s.snapshot_date,
                    s.total_loc,
                    s.total_functions,
                    s.cc_per_method,
                    s.cc_huge_count,
                    s.dup_ratio,
                    s.health_score,
                    s.lint_errors,
                    s.todo_count + s.fixme_count + s.hack_count,
                ]
            )
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=code_metrics.csv"
            },
        )
    else:
        return {
            "items": [
                {
                    "date": s.snapshot_date.isoformat(),
                    "total_loc": s.total_loc,
                    "total_functions": s.total_functions,
                    "cc_per_method": s.cc_per_method,
                    "cc_huge_count": s.cc_huge_count,
                    "dup_ratio": s.dup_ratio,
                    "health_score": s.health_score,
                    "lint_errors": s.lint_errors,
                    "todo_count": s.todo_count + s.fixme_count + s.hack_count,
                }
                for s in snapshots
            ]
        }


@router.get("/compare", summary="版本对比")
async def compare_versions(
    current_user: CurrentUser,
    db: DbSession,
    tag_a: str = Query(..., description="第一个 tag/版本"),
    tag_b: str = Query(..., description="第二个 tag/版本"),
):
    """对比两个版本的代码度量数据"""
    results = {}
    for label, tag in [("a", tag_a), ("b", tag_b)]:
        stmt = (
            select(CodeMetricsSnapshot)
            .where(
                (CodeMetricsSnapshot.tag == tag)
                | (CodeMetricsSnapshot.branch == tag)
            )
            .order_by(CodeMetricsSnapshot.snapshot_date.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        snap = result.scalar_one_or_none()
        if snap:
            results[label] = {
                "tag": tag,
                "snapshot_date": snap.snapshot_date.isoformat(),
                "total_loc": snap.total_loc,
                "total_functions": snap.total_functions,
                "cc_per_method": snap.cc_per_method,
                "cc_maximum": snap.cc_maximum,
                "cc_huge_count": snap.cc_huge_count,
                "dup_ratio": snap.dup_ratio,
                "health_score": snap.health_score,
                "lint_errors": snap.lint_errors,
                "unsafe_functions_count": snap.unsafe_functions_count,
                "todo_count": snap.todo_count + snap.fixme_count + snap.hack_count,
            }
        else:
            results[label] = None

    if not results.get("a") or not results.get("b"):
        return {"error": "One or both tags not found", "results": results}

    # Calculate deltas
    a, b = results["a"], results["b"]
    deltas = {}
    for key in [
        "total_loc",
        "total_functions",
        "cc_per_method",
        "cc_huge_count",
        "dup_ratio",
        "health_score",
        "lint_errors",
        "unsafe_functions_count",
        "todo_count",
    ]:
        deltas[key] = (b.get(key, 0) or 0) - (a.get(key, 0) or 0)

    return {"a": a, "b": b, "deltas": deltas}


@router.post("/cleanup", summary="清理过期明细数据（管理员）")
async def cleanup_old_details(
    current_user: CurrentAdminUser,
    db: DbSession,
    retention_days: int = Query(365, ge=30, le=3650),
):
    """清理超过保留期限的明细数据"""
    cutoff_date = date.today() - timedelta(days=retention_days)

    # 找到过期快照 ID
    old_snapshots = await db.execute(
        select(CodeMetricsSnapshot.id).where(
            CodeMetricsSnapshot.snapshot_date < cutoff_date
        )
    )
    old_ids = [row[0] for row in old_snapshots]

    if not old_ids:
        return {"deleted": 0, "message": "No expired data found"}

    # 删除关联明细
    await db.execute(
        delete(CodeComplexityDetail).where(
            CodeComplexityDetail.snapshot_id.in_(old_ids)
        )
    )
    await db.execute(
        delete(CodeDuplicationDetail).where(
            CodeDuplicationDetail.snapshot_id.in_(old_ids)
        )
    )
    await db.execute(
        delete(CodeSecurityDetail).where(
            CodeSecurityDetail.snapshot_id.in_(old_ids)
        )
    )
    # 删除快照本身
    await db.execute(
        delete(CodeMetricsSnapshot).where(CodeMetricsSnapshot.id.in_(old_ids))
    )
    await db.commit()

    return {"deleted": len(old_ids), "retention_days": retention_days}


@router.get("/derived-metrics", summary="衍生指标（PR 维度聚合）")
async def get_derived_metrics(
    current_user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
):
    """从 PR 数据聚合衍生指标：PR 大小分布、代码变更量、修改类型分布"""
    from infrastructure.persistence.models import PullRequest

    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = select(PullRequest).where(
        PullRequest.owner == "vllm-project",
        PullRequest.repo == "vllm-ascend",
        PullRequest.created_at >= cutoff,
    )
    result = await db.execute(stmt)
    prs = result.scalars().all()

    # PR 大小分布
    size_dist = {"XS(<10)": 0, "S(10-50)": 0, "M(50-200)": 0, "L(200-500)": 0, "XL(>500)": 0}
    total_add = 0
    total_del = 0
    type_dist: Counter = Counter()

    for pr in prs:
        additions = pr.additions or 0
        deletions = pr.deletions or 0
        total_add += additions
        total_del += deletions

        if additions < 10:
            size_dist["XS(<10)"] += 1
        elif additions < 50:
            size_dist["S(10-50)"] += 1
        elif additions < 200:
            size_dist["M(50-200)"] += 1
        elif additions < 500:
            size_dist["L(200-500)"] += 1
        else:
            size_dist["XL(>500)"] += 1

        # 修改类型从 data JSON 提取
        data = pr.data or {}
        commit_types = data.get("commit_types", [])
        if isinstance(commit_types, list):
            for ct in commit_types:
                if isinstance(ct, str):
                    type_dist[ct] += 1
        elif isinstance(commit_types, str):
            type_dist[commit_types] += 1

    return {
        "pr_count": len(prs),
        "total_additions": total_add,
        "total_deletions": total_del,
        "size_distribution": size_dist,
        "type_distribution": dict(type_dist.most_common(10)),
    }


@router.post("/trigger", summary="手动触发 CI 采集（管理员）")
async def trigger_collection(
    current_user: CurrentAdminUser,
    db: DbSession,
    branch: str = Query("main", description="目标分支"),
    tag: str = Query("", description="目标 tag（可选）"),
):
    """触发 vllm-ascend CI 中的代码度量采集工作流"""
    try:
        from infrastructure.clients.github_client import GitHubClient
        from infrastructure.core.config import settings

        client = GitHubClient()
        # 尝试触发 code-metrics workflow
        url = f"/repos/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/workflows/code-metrics.yml/dispatches"
        payload = {"ref": branch, "inputs": {"branch": branch, "tag": tag}}
        await client._request("POST", url, data=payload)
        return {"status": "triggered", "branch": branch, "tag": tag}
    except Exception as e:
        logger.warning(f"Failed to trigger code-metrics workflow: {e}")
        return {
            "status": "failed",
            "message": "CI 工作流未配置或触发失败，请在 vllm-ascend 仓库创建 code-metrics.yml 工作流",
            "branch": branch,
        }


@router.post("/collect", summary="本地采集代码度量（管理员）")
async def collect_locally(
    current_user: CurrentAdminUser,
    db: DbSession,
    branch: str = Query("main", description="目标分支"),
):
    """在 Dashboard 服务器上直接运行 cloc/lizard/jscpd 采集代码度量"""
    from infrastructure.tasks.task_manager import TaskManager

    task_id = await TaskManager.create_task(
        db,
        "code_metrics_collect",
        {"branch": branch},
        f"code_metrics:manual:{branch}",
        required_capability="python",
        priority=10,
    )
    await db.commit()
    return {
        "status": "queued" if task_id else "already_queued",
        "task_id": task_id,
        "branch": branch,
    }


@router.get("/alerts", summary="代码度量告警检查")
async def check_alerts(
    current_user: CurrentUser,
    db: DbSession,
):
    """检查代码度量告警：健康度降级、新增超大复杂度函数"""
    # 获取最近两个快照
    stmt = select(CodeMetricsSnapshot).order_by(
        CodeMetricsSnapshot.snapshot_date.desc()
    ).limit(2)
    result = await db.execute(stmt)
    snapshots = result.scalars().all()

    alerts = []

    if len(snapshots) >= 2:
        latest, prev = snapshots[0], snapshots[1]

        # 健康度降级 > 10 分
        if prev.health_score and latest.health_score:
            drop = prev.health_score - latest.health_score
            if drop > 10:
                alerts.append({
                    "level": "warning",
                    "type": "health_score_drop",
                    "message": f"健康度评分从 {prev.health_score} 降至 {latest.health_score}（下降 {drop:.1f} 分）",
                    "snapshot_date": latest.snapshot_date.isoformat() if latest.snapshot_date else None,
                })

        # 新增超大复杂度函数
        if latest.cc_huge_count and prev.cc_huge_count:
            new_huge = latest.cc_huge_count - prev.cc_huge_count
            if new_huge > 0:
                alerts.append({
                    "level": "warning",
                    "type": "new_huge_complexity",
                    "message": f"新增 {new_huge} 个超大复杂度函数（当前共 {latest.cc_huge_count} 个）",
                    "snapshot_date": latest.snapshot_date.isoformat() if latest.snapshot_date else None,
                })

        # 重复率上升 > 2%
        if prev.dup_ratio and latest.dup_ratio:
            rise = latest.dup_ratio - prev.dup_ratio
            if rise > 2:
                alerts.append({
                    "level": "info",
                    "type": "duplication_rise",
                    "message": f"代码重复率从 {prev.dup_ratio:.2f}% 升至 {latest.dup_ratio:.2f}%（上升 {rise:.2f}%）",
                    "snapshot_date": latest.snapshot_date.isoformat() if latest.snapshot_date else None,
                })

        # 不安全函数新增
        if latest.unsafe_functions_count and prev.unsafe_functions_count:
            new_unsafe = latest.unsafe_functions_count - prev.unsafe_functions_count
            if new_unsafe > 0:
                alerts.append({
                    "level": "error",
                    "type": "new_unsafe_functions",
                    "message": f"新增 {new_unsafe} 个不安全函数调用",
                    "snapshot_date": latest.snapshot_date.isoformat() if latest.snapshot_date else None,
                })

    return {"alerts": alerts, "count": len(alerts)}


@router.get("/ci-correlation", summary="CI 关联分析")
async def get_ci_correlation(
    current_user: CurrentUser,
    db: DbSession,
    days: int = Query(30, ge=1, le=365),
):
    """分析代码度量与 CI 结果的关联性"""
    from sqlalchemy import func as sql_func

    from infrastructure.persistence.models import CIResult

    cutoff = date.today() - timedelta(days=days)

    # 获取度量快照
    metrics_stmt = select(CodeMetricsSnapshot).where(
        CodeMetricsSnapshot.snapshot_date >= cutoff
    ).order_by(CodeMetricsSnapshot.snapshot_date.asc())
    metrics_result = await db.execute(metrics_stmt)
    snapshots = metrics_result.scalars().all()

    # 获取 CI 数据（按天聚合）
    ci_stmt = select(
        sql_func.date(CIResult.started_at).label("day"),
        sql_func.count(CIResult.id).label("total"),
        sql_func.sum(sql_func.case((CIResult.conclusion == "success", 1), else_=0)).label("success"),
    ).where(
        CIResult.started_at >= cutoff
    ).group_by(sql_func.date(CIResult.started_at)).order_by(sql_func.date(CIResult.started_at).asc())
    ci_result = await db.execute(ci_stmt)
    ci_by_date = {str(row.day): {"total": row.total, "success": row.success, "rate": (row.success / row.total * 100) if row.total > 0 else 0} for row in ci_result}

    # 合并数据
    correlation_data = []
    for snap in snapshots:
        date_str = snap.snapshot_date.isoformat() if snap.snapshot_date else None
        ci = ci_by_date.get(date_str, {})
        correlation_data.append({
            "date": date_str,
            "cc_huge_count": snap.cc_huge_count,
            "dup_ratio": snap.dup_ratio,
            "health_score": snap.health_score,
            "ci_total": ci.get("total", 0),
            "ci_success_rate": round(ci.get("rate", 0), 1),
        })

    return {
        "items": correlation_data,
        "summary": {
            "snapshots": len(snapshots),
            "ci_days": len(ci_by_date),
            "matched_days": len(correlation_data),
        }
    }


@router.get("/snapshot/{snapshot_id}", summary="快照详情（下钻）")
async def get_snapshot_detail(
    snapshot_id: int,
    current_user: CurrentUser,
    db: DbSession,
):
    """获取指定快照的完整详情"""
    stmt = select(CodeMetricsSnapshot).where(CodeMetricsSnapshot.id == snapshot_id)
    result = await db.execute(stmt)
    snap = result.scalar_one_or_none()
    if not snap:
        return {"error": "Snapshot not found"}
    return {
        "id": snap.id,
        "snapshot_date": snap.snapshot_date.isoformat() if snap.snapshot_date else None,
        "collection_status": snap.collection_status,
        "health_score": snap.health_score,
        "health_scores": {
            "complexity": snap.health_score_complexity,
            "security": snap.health_score_security,
            "duplication": snap.health_score_duplication,
            "method_size": snap.health_score_method_size,
            "tech_debt": snap.health_score_tech_debt,
            "lint": snap.health_score_lint,
        },
        "metrics": {
            "total_loc": snap.total_loc,
            "total_functions": snap.total_functions,
            "total_files": snap.total_files,
            "cc_per_method": snap.cc_per_method,
            "cc_maximum": snap.cc_maximum,
            "cc_huge_count": snap.cc_huge_count,
            "dup_ratio": snap.dup_ratio,
            "dup_blocks": snap.dup_blocks,
            "unsafe_functions_count": snap.unsafe_functions_count,
            "lint_errors": snap.lint_errors,
            "todo_count": snap.todo_count,
            "fixme_count": snap.fixme_count,
            "hack_count": snap.hack_count,
        },
        "language_loc": snap.language_loc or {},
        "module_loc": snap.module_loc or {},
    }


@router.get("/complexity/file", summary="文件级复杂度详情（下钻）")
async def get_file_complexity(
    current_user: CurrentUser,
    db: DbSession,
    file_path: str = Query(..., description="文件路径"),
    limit: int = Query(50, ge=1, le=200),
):
    """获取指定文件的所有函数复杂度"""
    latest = await db.execute(
        select(CodeMetricsSnapshot.id).order_by(
            CodeMetricsSnapshot.snapshot_date.desc()
        ).limit(1)
    )
    snapshot_id = latest.scalar_one_or_none()
    if not snapshot_id:
        return {"items": []}

    stmt = (
        select(CodeComplexityDetail)
        .where(
            CodeComplexityDetail.snapshot_id == snapshot_id,
            CodeComplexityDetail.file_path == file_path,
        )
        .order_by(CodeComplexityDetail.cyclomatic_complexity.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return {
        "file_path": file_path,
        "items": [
            {
                "function_name": r.function_name,
                "cyclomatic_complexity": r.cyclomatic_complexity,
                "max_nesting_depth": r.max_nesting_depth,
                "function_lines": r.function_lines,
                "start_line": r.start_line,
            }
            for r in result.scalars().all()
        ],
    }


@router.get("/heatmap/file", summary="文件变更历史（下钻）")
async def get_file_heatmap_detail(
    current_user: CurrentUser,
    db: DbSession,
    file_path: str = Query(..., description="文件路径"),
):
    """获取指定文件的变更历史"""
    stmt = select(CodeMetricsFileHeatmap).where(
        CodeMetricsFileHeatmap.file_path == file_path,
    )
    result = await db.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        return {"error": "File not found in heatmap"}
    return {
        "file_path": record.file_path,
        "change_count": record.change_count,
        "bug_fix_count": record.bug_fix_count,
        "last_changed": record.last_changed.isoformat() if record.last_changed else None,
        "last_commit_sha": record.last_commit_sha,
    }


# ===========================================================================
# 下钻明细：文件列表 / 函数列表 / 维度聚合
#
# 注意：以下 _KNOWN_MODULES 与前端 CodeMetricsBoard.tsx 的 KNOWN_MODULE_SEGMENTS
# 保持同步，新增模块时请同时修改两端。
# ===========================================================================

_KNOWN_MODULES = ("vllm_ascend", "csrc", "tests", "benchmarks", "tools", "docs", "examples", "configs")


def _infer_language_from_path(file_path: str) -> str:
    """根据扩展名推断语言。"""
    if not file_path:
        return "Unknown"
    lower = file_path.lower()
    if lower.endswith(".py"):
        return "Python"
    if lower.endswith((".cpp", ".cc", ".cxx", ".c++")):
        return "C++"
    if lower.endswith(".c"):
        return "C"
    if lower.endswith((".h", ".hpp", ".hxx")):
        return "C/C++ Header"
    if lower.endswith(".cmake") or lower.endswith("cmakelists.txt"):
        return "CMake"
    if lower.endswith(".sh"):
        return "Shell"
    return "Unknown"


def _derive_module(file_path: str) -> str:
    """从文件路径中推断所属模块（顶级目录名）。

    支持绝对路径和相对路径。若无法识别则返回 "other"。
    注意：`vllm-ascend`（带连字符）是仓库名，不应匹配；`vllm_ascend`（带下划线）才是模块名。
    """
    if not file_path:
        return "other"
    # 规范化：替换反斜杠，取分割后的段
    norm = file_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p and p != "."]
    # 精确匹配已知模块名（避免把仓库根 vllm-ascend 误识别为 vllm_ascend）
    for seg in parts:
        if seg in _KNOWN_MODULES:
            return seg
    # 没有匹配到已知模块：取第一个"目录"段（跳过文件名本身）
    dir_segments = parts[:-1]  # 排除最后一段（文件名）
    if not dir_segments:
        return "other"
    first = dir_segments[0]
    # 若第一段像文件名（含点），归为 other
    if "." in first:
        return "other"
    return first


def _aggregate_by_file(rows) -> dict[str, dict]:
    """将 CodeComplexityDetail 行列表按 file_path 聚合。

    返回 {file_path: {file_path, language, module, function_count,
    total_complexity, max_complexity, total_function_lines}}。
    供 list_files / get_drilldown 复用，避免重复逻辑。
    """
    agg: dict[str, dict] = {}
    for r in rows:
        fp = r.file_path or ""
        lang = r.language or _infer_language_from_path(fp)
        mod = _derive_module(fp)

        bucket = agg.setdefault(
            fp,
            {
                "file_path": fp,
                "language": lang,
                "module": mod,
                "function_count": 0,
                "total_complexity": 0,
                "max_complexity": 0,
                "total_function_lines": 0,
            },
        )
        bucket["function_count"] += 1
        cc = r.cyclomatic_complexity or 0
        bucket["total_complexity"] += cc
        if cc > bucket["max_complexity"]:
            bucket["max_complexity"] = cc
        bucket["total_function_lines"] += r.function_lines or 0
    return agg


async def _get_latest_snapshot_id(db) -> int | None:
    """获取最新快照 ID。同日多快照时按 id 降序取最新（确定性 tiebreaker）。"""
    latest = await db.execute(
        select(CodeMetricsSnapshot.id).order_by(
            CodeMetricsSnapshot.snapshot_date.desc(),
            CodeMetricsSnapshot.id.desc(),
        ).limit(1)
    )
    return latest.scalar_one_or_none()


@router.get("/files", summary="文件列表（下钻）")
async def list_files(
    current_user: CurrentUser,
    db: DbSession,
    language: str | None = Query(None, description="按语言过滤（下推 SQL）"),
    module: str | None = Query(None, description="按模块过滤（Python 层，派生自 file_path）"),
    search: str | None = Query(None, description="文件路径模糊搜索（Python 层）"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取最新快照的文件列表（基于复杂度明细聚合）。

    返回每个文件的：语言、模块、函数数、总复杂度、最大复杂度、函数行数总和。

    性能说明：
    - ``language`` 过滤下推到 SQL（``CodeComplexityDetail.language`` 是存储列）。
    - ``module`` / ``search`` 非存储列，在 Python 层过滤。
    - ``limit`` / ``offset`` 仅对**聚合后**的结果分页，不限制 DB 扫描量。
      若需限制内部扫描量，请先通过 ``language`` 收窄。
    """
    snapshot_id = await _get_latest_snapshot_id(db)
    if not snapshot_id:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    # 基础查询：snapshot_id + language 下推 SQL
    stmt = select(CodeComplexityDetail).where(
        CodeComplexityDetail.snapshot_id == snapshot_id
    )
    if language:
        stmt = stmt.where(CodeComplexityDetail.language == language)

    rows = (await db.execute(stmt)).scalars().all()

    # 聚合（复用共享辅助函数）
    agg = _aggregate_by_file(rows)

    # Python 层过滤 module / search（非存储列）
    items = []
    for item in agg.values():
        if module and item["module"].lower() != module.lower():
            continue
        if search and search.lower() not in item["file_path"].lower():
            continue
        items.append(item)

    items.sort(key=lambda x: x["total_complexity"], reverse=True)
    total = len(items)
    paged = items[offset : offset + limit]
    return {"items": paged, "total": total, "limit": limit, "offset": offset}


@router.get("/functions", summary="函数列表（下钻）")
async def list_functions(
    current_user: CurrentUser,
    db: DbSession,
    language: str | None = Query(None, description="按语言过滤（下推 SQL）"),
    module: str | None = Query(None, description="按模块过滤（Python 层，派生自 file_path）"),
    file_path: str | None = Query(None, description="按文件路径过滤（精确匹配，下推 SQL）"),
    search: str | None = Query(None, description="函数名/文件路径模糊搜索（Python 层）"),
    min_complexity: int | None = Query(None, ge=0, description="最小圈复杂度（下推 SQL）"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取最新快照的函数列表，支持多维度过滤。

    性能说明：
    - ``language`` / ``file_path`` / ``min_complexity`` 下推到 SQL。
    - ``module`` / ``search`` 在 Python 层过滤。
    - ``limit`` / ``offset`` 仅对过滤后的结果分页；先用 SQL 可下推的参数收窄可减少内存负载。
    """
    snapshot_id = await _get_latest_snapshot_id(db)
    if not snapshot_id:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    stmt = select(CodeComplexityDetail).where(
        CodeComplexityDetail.snapshot_id == snapshot_id
    )
    if language:
        stmt = stmt.where(CodeComplexityDetail.language == language)
    if file_path:
        stmt = stmt.where(CodeComplexityDetail.file_path == file_path)
    if min_complexity is not None:
        stmt = stmt.where(CodeComplexityDetail.cyclomatic_complexity >= min_complexity)

    rows = (await db.execute(stmt)).scalars().all()

    filtered = []
    for r in rows:
        fp = r.file_path or ""
        lang = r.language or _infer_language_from_path(fp)
        mod = _derive_module(fp)

        if module and mod.lower() != module.lower():
            continue
        if search:
            s = search.lower()
            if s not in (r.function_name or "").lower() and s not in fp.lower():
                continue

        filtered.append(
            {
                "file_path": fp,
                "function_name": r.function_name,
                "language": lang,
                "module": mod,
                "cyclomatic_complexity": r.cyclomatic_complexity,
                "max_nesting_depth": r.max_nesting_depth,
                "function_lines": r.function_lines,
                "start_line": r.start_line,
            }
        )

    # 默认按复杂度降序
    filtered.sort(key=lambda x: x["cyclomatic_complexity"] or 0, reverse=True)
    total = len(filtered)
    paged = filtered[offset : offset + limit]
    return {"items": paged, "total": total, "limit": limit, "offset": offset}


@router.get("/drilldown", summary="维度聚合下钻（语言/模块）")
async def get_drilldown(
    current_user: CurrentUser,
    db: DbSession,
    language: str | None = Query(None, description="按语言下钻（下推 SQL）"),
    module: str | None = Query(None, description="按模块下钻（Python 层）"),
    top_n: int = Query(10, ge=1, le=50),
):
    """聚合指定维度（语言或模块）的明细统计。

    返回：文件数、函数数、函数总行数、平均/最大复杂度、Top 文件、Top 复杂函数。
    结合快照中的 language_loc / module_loc 提供 LOC 信息。

    性能说明：``language`` 下推 SQL；``module`` 在 Python 层过滤。
    """
    snapshot_id = await _get_latest_snapshot_id(db)
    if not snapshot_id:
        return {"has_data": False}

    # 拿快照获取 LOC
    snap_stmt = select(CodeMetricsSnapshot).where(CodeMetricsSnapshot.id == snapshot_id)
    snap = (await db.execute(snap_stmt)).scalar_one_or_none()
    if not snap:
        return {"has_data": False}

    loc_map = snap.language_loc or {}
    mod_loc_map = snap.module_loc or {}

    # 过滤复杂度明细：language 下推 SQL
    detail_stmt = select(CodeComplexityDetail).where(
        CodeComplexityDetail.snapshot_id == snapshot_id
    )
    if language:
        detail_stmt = detail_stmt.where(CodeComplexityDetail.language == language)
    rows = (await db.execute(detail_stmt)).scalars().all()

    file_set: set[str] = set()
    func_count = 0
    total_cc = 0
    max_cc = 0
    total_fn_lines = 0
    file_agg: dict[str, dict] = {}
    func_list: list[dict] = []

    for r in rows:
        fp = r.file_path or ""
        lang = r.language or _infer_language_from_path(fp)
        mod = _derive_module(fp)

        # module 在 Python 层过滤（派生自 file_path，非存储列）
        if module and mod.lower() != module.lower():
            continue

        file_set.add(fp)
        func_count += 1
        cc = r.cyclomatic_complexity or 0
        total_cc += cc
        if cc > max_cc:
            max_cc = cc
        total_fn_lines += r.function_lines or 0

        bucket = file_agg.setdefault(
            fp,
            {
                "file_path": fp,
                "language": lang,
                "module": mod,
                "function_count": 0,
                "total_complexity": 0,
                "total_function_lines": 0,
            },
        )
        bucket["function_count"] += 1
        bucket["total_complexity"] += cc
        bucket["total_function_lines"] += r.function_lines or 0

        func_list.append(
            {
                "file_path": fp,
                "function_name": r.function_name,
                "language": lang,
                "module": mod,
                "cyclomatic_complexity": cc,
                "max_nesting_depth": r.max_nesting_depth,
                "function_lines": r.function_lines,
                "start_line": r.start_line,
            }
        )

    # LOC 信息：language/module 均带大小写回退（map key 可能是 "Python"/"python" 或 "csrc"/"CSRC"）
    loc = 0
    if language:
        loc = loc_map.get(language, 0) or loc_map.get(language.capitalize(), 0)
    elif module:
        loc = mod_loc_map.get(module, 0) or mod_loc_map.get(module.lower(), 0) or mod_loc_map.get(module.capitalize(), 0)

    top_files = sorted(
        file_agg.values(), key=lambda x: x["total_complexity"], reverse=True
    )[:top_n]
    top_functions = sorted(
        func_list, key=lambda x: x["cyclomatic_complexity"] or 0, reverse=True
    )[:top_n]

    return {
        "has_data": True,
        "filter": {"language": language, "module": module},
        "loc": loc,
        "file_count": len(file_set),
        "function_count": func_count,
        "total_function_lines": total_fn_lines,
        "avg_complexity": round(total_cc / func_count, 2) if func_count else 0,
        "max_complexity": max_cc,
        "top_files": top_files,
        "top_functions": top_functions,
    }
