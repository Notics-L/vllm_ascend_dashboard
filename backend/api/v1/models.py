"""
模型管理 API 路由
支持模型配置管理、报告查看、趋势分析、报告对比等功能
"""
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

logger = logging.getLogger(__name__)

from api.deps import CurrentAdminUser, DbSession
from contracts.schemas import (
    Message,
    ModelComparisonResponse,
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ModelReportResponse,
    ModelReportUpdate,
    ModelTrendData,
)
from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.persistence.models import ModelConfig, ModelReport
from model_sync.model_sync_service import ModelSyncService
from model_sync.model_trend_service import ModelTrendService
from tooling.generators.startup_command_generator import StartupCommandGenerator

router = APIRouter()


# ============ 模型每日报告 ============

class ModelDailyReport(BaseModel):
    """模型每日报告"""
    date: str
    summary: dict
    model_results: list[dict]
    markdown_report: str


@router.get("/reports/daily/{date}", response_model=ModelDailyReport)
async def get_model_daily_report(
    date: str,
    db: DbSession
):
    """
    获取指定日期的模型每日报告

    Args:
        date: 日期，格式 YYYY-MM-DD (北京时间)

    Returns:
        ModelDailyReport: 包含统计数据和模型报告
    """
    try:
        # 解析日期
        try:
            report_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD"
            )

        # 获取当天的起始和结束时间（数据库存储的是北京时间，无时区信息）
        start_datetime = datetime.combine(report_date, datetime.min.time())
        end_datetime = start_datetime + timedelta(days=1)

        # 查询当天的模型报告
        stmt = (
            select(ModelReport)
            .join(ModelConfig, ModelReport.model_config_id == ModelConfig.id)
            .where(
                ModelReport.created_at >= start_datetime,
                ModelReport.created_at < end_datetime,
            )
            .order_by(ModelReport.created_at.desc())
        )

        result = await db.execute(stmt)
        reports = result.scalars().all()

        # 统计数据
        total_reports = len(reports)
        success_reports = sum(1 for r in reports if r.pass_fail == 'pass' or r.pass_fail is True)
        failure_reports = sum(1 for r in reports if r.pass_fail == 'fail' or r.pass_fail is False)
        success_rate = round((success_reports / total_reports * 100) if total_reports > 0 else 0.0, 2)

        # 按模型分组统计
        model_stats = {}
        for report in reports:
            model_id = report.model_config_id
            if model_id not in model_stats:
                # 获取模型信息
                model_stmt = select(ModelConfig).where(ModelConfig.id == model_id)
                model_result = await db.execute(model_stmt)
                model = model_result.scalar_one_or_none()

                model_stats[model_id] = {
                    "model_id": model_id,
                    "model_name": model.model_name if model else "Unknown",
                    "series": model.series if model else None,
                    "total_reports": 0,
                    "success_reports": 0,
                    "failure_reports": 0,
                    "latest_report": None,
                }

            stats = model_stats[model_id]
            stats["total_reports"] += 1
            if report.pass_fail == 'pass' or report.pass_fail is True:
                stats["success_reports"] += 1
            elif report.pass_fail == 'fail' or report.pass_fail is False:
                stats["failure_reports"] += 1

            # 更新最新报告
            if stats["latest_report"] is None:
                needs_update = True
            elif report.created_at:
                latest_created_at = stats["latest_report"].get("created_at")
                if latest_created_at and report.created_at.isoformat() > latest_created_at:
                    needs_update = True
                else:
                    needs_update = False
            else:
                needs_update = False

            if needs_update:
                # 解析指标
                metrics = {}
                if report.metrics_json:
                    try:
                        metrics = json.loads(report.metrics_json) if isinstance(report.metrics_json, str) else report.metrics_json
                    except (json.JSONDecodeError, TypeError):
                        pass

                if not metrics and report.report_json:
                    try:
                        report_data = json.loads(report.report_json) if isinstance(report.report_json, str) else report.report_json
                        if isinstance(report_data, dict):
                            metrics = report_data.get('metrics', {})
                    except (json.JSONDecodeError, TypeError):
                        pass

                # 构建 GitHub URL
                github_url = None
                if report.workflow_run_id:
                    github_url = f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{report.workflow_run_id}"

                stats["latest_report"] = {
                    "report_id": report.id,
                    "status": "success" if (report.pass_fail == 'pass' or report.pass_fail is True) else "failure" if (report.pass_fail == 'fail' or report.pass_fail is False) else None,
                    "accuracy": metrics.get('accuracy') or metrics.get('avg_accuracy'),
                    "throughput": metrics.get('throughput') or metrics.get('avg_throughput'),
                    "first_token_latency": metrics.get('first_token_latency') or metrics.get('avg_first_token_latency'),
                    "created_at": report.created_at.isoformat() if report.created_at else None,
                    "github_html_url": github_url,
                }

        # 生成 Markdown 报告
        markdown_lines = [
            "# 模型每日报告",
            "",
            f"## {date}",
            "",
            "### 汇总",
            "",
            f"- 总报告数：{total_reports}",
            f"- 成功：{success_reports}",
            f"- 失败：{failure_reports}",
            f"- 成功率：{success_rate}%",
            "",
            "### 模型详情",
            "",
        ]

        for _model_id, stats in model_stats.items():
            latest = stats["latest_report"]
            status_emoji = "✅" if latest and latest["status"] == "success" else "❌" if latest and latest["status"] == "failure" else "⏳"
            markdown_lines.append(f"#### {status_emoji} {stats['model_name']}")
            markdown_lines.append(f"- 系列：{stats['series'] or 'N/A'}")
            markdown_lines.append(f"- 状态：{latest['status'] or '暂无报告'}")
            if latest:
                if latest.get('accuracy'):
                    markdown_lines.append(f"- 准确率：{latest['accuracy'] * 100:.2f}%")
                if latest.get('throughput'):
                    markdown_lines.append(f"- 吞吐量：{latest['throughput']:.2f} tok/s")
                if latest.get('first_token_latency'):
                    markdown_lines.append(f"- 首 Token 延迟：{latest['first_token_latency']:.2f} ms")
            markdown_lines.append("")

        markdown_report = "\n".join(markdown_lines)

        return ModelDailyReport(
            date=date,
            summary={
                "total_reports": total_reports,
                "success_reports": success_reports,
                "failure_reports": failure_reports,
                "success_rate": success_rate,
            },
            model_results=list(model_stats.values()),
            markdown_report=markdown_report,
        )

    except Exception as e:
        logger.error(f"Failed to get model daily report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get model daily report: {str(e)}"
        )


# ============ 模型最新结果 ============

class ModelLatestResult(BaseModel):
    """模型最新结果"""
    model_id: int
    model_name: str
    series: str | None = None
    report_id: int | None = None
    status: str | None = None  # success, failure, running
    accuracy: float | None = None
    throughput: float | None = None
    first_token_latency: float | None = None
    created_at: str | None = None
    github_html_url: str | None = None


@router.get("/latest-results", response_model=list[ModelLatestResult])
async def get_models_latest_results(db: DbSession):
    """获取所有模型的最新报告结果"""
    results = []

    # 获取所有模型
    stmt = select(ModelConfig).order_by(ModelConfig.model_name)
    result = await db.execute(stmt)
    models = result.scalars().all()

    for model in models:
        # 获取每个模型的最新报告
        report_stmt = (
            select(ModelReport)
            .where(ModelReport.model_config_id == model.id)
            .order_by(ModelReport.created_at.desc())
            .limit(1)
        )
        report_result = await db.execute(report_stmt)
        report = report_result.scalar_one_or_none()

        if report:
            # 解析关键指标
            accuracy = None
            throughput = None
            first_token_latency = None

            # 首先尝试从 metrics_json 中获取
            metrics = None
            if report.metrics_json:
                try:
                    metrics = json.loads(report.metrics_json) if isinstance(report.metrics_json, str) else report.metrics_json
                except (json.JSONDecodeError, TypeError):
                    pass

            # 如果 metrics_json 为空，尝试从 report_json 中获取
            if not metrics and report.report_json:
                try:
                    report_data = json.loads(report.report_json) if isinstance(report.report_json, str) else report.report_json
                    if isinstance(report_data, dict):
                        metrics = report_data.get('metrics', {})
                except (json.JSONDecodeError, TypeError):
                    pass

            # 从 metrics 中提取指标
            if metrics and isinstance(metrics, dict):
                for key in ['accuracy', 'avg_accuracy', 'overall_accuracy']:
                    if key in metrics:
                        try:
                            accuracy = float(metrics[key])
                            break
                        except (ValueError, TypeError):
                            continue

                for key in ['throughput', 'avg_throughput', 'overall_throughput']:
                    if key in metrics:
                        try:
                            throughput = float(metrics[key])
                            break
                        except (ValueError, TypeError):
                            continue

                for key in ['first_token_latency', 'avg_first_token_latency', 'ttft']:
                    if key in metrics:
                        try:
                            first_token_latency = float(metrics[key])
                            break
                        except (ValueError, TypeError):
                            continue

            # 构建 GitHub URL
            github_url = None
            if report.workflow_run_id:
                github_url = f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{report.workflow_run_id}"

            # 确定状态（pass_fail 可能是字符串 'pass'/'fail' 或布尔值）
            status = None
            if report.pass_fail is True or report.pass_fail == 'pass':
                status = 'success'
            elif report.pass_fail is False or report.pass_fail == 'fail':
                status = 'failure'

            results.append(ModelLatestResult(
                model_id=model.id,
                model_name=model.model_name,
                series=model.series,
                report_id=report.id,
                status=status,
                accuracy=accuracy,
                throughput=throughput,
                first_token_latency=first_token_latency,
                created_at=report.created_at.isoformat() if report.created_at else None,
                github_html_url=github_url,
            ))
        else:
            # 没有报告的模型
            results.append(ModelLatestResult(
                model_id=model.id,
                model_name=model.model_name,
                series=model.series,
            ))

    return results


# ============ 模型配置 CRUD ============

def parse_model_json_fields(model: ModelConfig) -> dict:
    """解析模型的 JSON 字段"""
    model_dict = {
        'id': model.id,
        'model_name': model.model_name,
        'series': model.series,
        'config_yaml': model.config_yaml,
        'status': model.status,
        'created_at': model.created_at,
        'updated_at': model.updated_at,
        'official_doc_url': model.official_doc_url,
    }

    # 解析 JSON 字符串为字典
    for field in ['key_metrics_config', 'pass_threshold', 'startup_commands']:
        value = getattr(model, field, None)
        if value:
            try:
                model_dict[field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                model_dict[field] = None
        else:
            model_dict[field] = None

    return model_dict


@router.get("", response_model=list[ModelConfigResponse])
async def list_models(
    db: DbSession,
    series: str | None = Query(None, description="模型系列过滤"),
    status: str | None = Query(None, description="状态过滤"),
    search: str | None = Query(None, description="搜索关键词")
):
    """获取模型列表（支持筛选和搜索）"""
    stmt = select(ModelConfig)

    if series:
        stmt = stmt.where(ModelConfig.series == series)
    if status:
        stmt = stmt.where(ModelConfig.status == status)
    if search:
        stmt = stmt.where(ModelConfig.model_name.contains(search))

    result = await db.execute(stmt)
    models = result.scalars().all()

    # 解析 JSON 字段
    return [parse_model_json_fields(model) for model in models]


# ============ 支持矩阵（必须在 /{model_id} 之前定义） ============

@router.get("/support-matrix")
async def get_support_matrix_endpoint_primary(
    db: DbSession,
    model_type: str | None = Query(None),
    role: str | None = Query(None),
    series: str | None = Query(None),
    support_status: str | None = Query(None),
    tier: str | None = Query(None),
):
    """获取完整支持矩阵（模型×特性交叉表）"""
    from support_matrix.support_matrix_sync import get_support_matrix as _get_matrix

    return await _get_matrix(
        db,
        model_type=model_type,
        role=role,
        series=series,
        support_status=support_status,
        tier=tier,
    )


@router.get("/feature-compatibility")
async def get_feature_compatibility_endpoint_primary(db: DbSession):
    """获取 25×25 特性互操作矩阵"""
    from support_matrix.support_matrix_sync import get_feature_compatibility as _get_compat

    return await _get_compat(db)


@router.get("/global-features")
async def get_global_features_endpoint_primary(db: DbSession):
    """获取全局功能支持状态（来自 supported_features.md）"""
    from support_matrix.support_matrix_sync import get_global_features as _get_gf

    return await _get_gf(db)


@router.get("/sync-status")
async def get_sync_status_endpoint_primary(db: DbSession):
    """获取最近同步状态"""
    from support_matrix.support_matrix_sync import get_sync_status as _get_ss

    return await _get_ss(db)


@router.post("/sync-upstream")
async def sync_upstream_endpoint_primary(
    db: DbSession,
    current_user: CurrentAdminUser,
    dry_run: bool = Query(False),
):
    """手动触发上游支持矩阵同步（管理员）

    参数 dry_run=True 时只返回 diff 不落库
    """
    from support_matrix.support_matrix_sync import sync_support_matrix as _sync

    result = await _sync(db, dry_run=dry_run)
    return result


# ============ 模型详情 ============

@router.get("/{model_id}", response_model=ModelConfigResponse)
async def get_model(db: DbSession, model_id: int):
    """获取模型详情"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 解析 JSON 字段
    model_dict = {
        'id': model.id,
        'model_name': model.model_name,
        'series': model.series,
        'config_yaml': model.config_yaml,
        'status': model.status,
        'created_at': model.created_at,
        'updated_at': model.updated_at,
    }

    # 解析 JSON 字符串为字典
    for field in ['key_metrics_config', 'pass_threshold', 'startup_commands']:
        value = getattr(model, field, None)
        if value:
            try:
                model_dict[field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                model_dict[field] = None
        else:
            model_dict[field] = None

    return model_dict


@router.post("", response_model=ModelConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    db: DbSession,
    model_data: ModelConfigCreate,
    current_user: CurrentAdminUser
):
    """创建模型配置"""
    # 检查是否已存在
    stmt = select(ModelConfig).where(
        ModelConfig.model_name == model_data.model_name
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型已存在",
        )

    # 创建模型
    model = ModelConfig(**model_data.model_dump(), created_by=current_user.id)
    db.add(model)
    await db.commit()
    await db.refresh(model)

    return parse_model_json_fields(model)


@router.put("/{model_id}", response_model=ModelConfigResponse)
async def update_model(
    db: DbSession,
    model_id: int,
    model_data: ModelConfigUpdate,
    current_user: CurrentAdminUser
):
    """更新模型配置"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 更新字段
    update_data = model_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        # JSON 字段需要序列化
        if field in ["key_metrics_config", "pass_threshold", "startup_commands"]:
            if value is not None:
                value = json.dumps(value)
        setattr(model, field, value)

    await db.commit()
    await db.refresh(model)

    return parse_model_json_fields(model)


@router.delete("/{model_id}", response_model=Message)
async def delete_model(
    db: DbSession,
    model_id: int,
    current_user: CurrentAdminUser
):
    """删除模型配置"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    db.delete(model)
    await db.commit()

    return {"message": "模型已成功删除"}


# ============ 模型报告相关 ============

@router.get("/{model_id}/reports", response_model=list[ModelReportResponse])
async def list_model_reports(
    db: DbSession,
    model_id: int,
    limit: int = Query(50, description="返回数量限制"),
    offset: int = Query(0, description="偏移量")
):
    """获取模型报告列表（按时间倒序）"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 查询报告，按时间倒序
    stmt = (
        select(ModelReport)
        .where(ModelReport.model_config_id == model_id)
        .order_by(ModelReport.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{model_id}/reports/latest", response_model=ModelReportResponse)
async def get_latest_report(db: DbSession, model_id: int):
    """获取最新模型报告"""
    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 直接查询报告，按时间倒序取第一个
    stmt = (
        select(ModelReport)
        .where(ModelReport.model_config_id == model_id)
        .order_by(ModelReport.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="暂无报告",
        )

    return report


@router.get("/{model_id}/reports/compare", response_model=ModelComparisonResponse)
async def compare_reports(
    db: DbSession,
    model_id: int,
    report_ids: str = Query(..., description="需要对比的报告 ID 列表，逗号分隔")
):
    """对比两个报告（支持新模板）"""
    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 解析 report_ids
    try:
        ids = [int(id.strip()) for id in report_ids.split(",")]
        if len(ids) != 2:
            raise ValueError("需要且仅需要 2 个报告 ID")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # 直接查询报告对象
    stmt = select(ModelReport).where(
        ModelReport.id.in_(ids),
        ModelReport.model_config_id == model_id
    )
    result = await db.execute(stmt)
    reports = result.scalars().all()

    if len(reports) < 2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定的报告",
        )

    # 计算指标变化
    report1 = reports[0]
    report2 = reports[1]

    # 解析 metrics
    metrics1 = report1.metrics_json if report1.metrics_json else {}
    metrics2 = report2.metrics_json if report2.metrics_json else {}

    # 兼容旧数据：如果是字符串，手动解析
    if isinstance(metrics1, str):
        import json
        metrics1 = json.loads(metrics1)
    if isinstance(metrics2, str):
        import json
        metrics2 = json.loads(metrics2)

    changes = ModelTrendService.calculate_changes(metrics1, metrics2)

    # 新模板支持：提取 task 级别的对比
    tasks_comparison = []
    # 检查是否有 tasks 数据（从 report_json 中获取，因为 tasks 可能存储在 report_json 中）
    tasks1 = report1.tasks if report1.tasks else (report1.report_json.get('tasks', []) if report1.report_json else [])
    tasks2 = report2.tasks if report2.tasks else (report2.report_json.get('tasks', []) if report2.report_json else [])

    # 调试日志
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"tasks1: {tasks1}")
    logger.info(f"tasks2: {tasks2}")
    logger.info(f"tasks1 from report_json: {report1.report_json.get('tasks', []) if report1.report_json else 'N/A'}")
    logger.info(f"tasks2 from report_json: {report2.report_json.get('tasks', []) if report2.report_json else 'N/A'}")

    if tasks1 and tasks2 and len(tasks1) > 0 and len(tasks2) > 0:
        tasks1_dict = {t['name']: t for t in tasks1}
        tasks2_dict = {t['name']: t for t in tasks2}

        for task_name in set(tasks1_dict.keys()) | set(tasks2_dict.keys()):
            task1 = tasks1_dict.get(task_name, {})
            task2 = tasks2_dict.get(task_name, {})

            # 计算 task 级别的指标变化
            task_metrics1 = task1.get('metrics', {})
            task_metrics2 = task2.get('metrics', {})
            task_changes = ModelTrendService.calculate_changes(task_metrics1, task_metrics2)

            # 标识 task 是否存在于两个报告中
            only_in_baseline = task_name not in tasks2_dict
            only_in_current = task_name not in tasks1_dict

            tasks_comparison.append({
                'name': task_name,
                'baseline': {
                    'metrics': task_metrics1,
                    'test_input': task1.get('test_input', {}),
                    'target': task1.get('target', {}),
                    'pass_fail': task1.get('pass_fail', 'N/A')
                },
                'current': {
                    'metrics': task_metrics2,
                    'test_input': task2.get('test_input', {}),
                    'target': task2.get('target', {}),
                    'pass_fail': task2.get('pass_fail', 'N/A')
                },
                'changes': task_changes,
                'only_in_baseline': only_in_baseline,  # 仅在基准报告中
                'only_in_current': only_in_current,  # 仅在当前报告中
                'is_common': not only_in_baseline and not only_in_current  # 两个报告都有
            })

    return {
        "reports": reports,
        "metrics_comparison": {
            str(report1.id): metrics1,
            str(report2.id): metrics2
        },
        "changes": changes,
        "tasks_comparison": tasks_comparison  # 新增：task 级别的对比
    }


@router.get("/{model_id}/reports/{report_id}", response_model=ModelReportResponse)
async def get_report(db: DbSession, model_id: int, report_id: int):
    """获取指定报告详情"""
    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 获取报告
    stmt = select(ModelReport).where(
        ModelReport.id == report_id,
        ModelReport.model_config_id == model_id
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="报告不存在",
        )

    return report


@router.put("/{model_id}/reports/{report_id}", response_model=ModelReportResponse)
async def update_report(
    db: DbSession,
    model_id: int,
    report_id: int,
    report_data: ModelReportUpdate,
    current_user: CurrentAdminUser
):
    """更新报告（管理员可修改 Pass/Fail 等）"""
    import logging
    logger = logging.getLogger(__name__)

    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 获取报告
    stmt = select(ModelReport).where(
        ModelReport.id == report_id,
        ModelReport.model_config_id == model_id
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="报告不存在",
        )

    # 更新字段
    update_data = report_data.model_dump(exclude_unset=True)

    # 先处理 report_json，合并所有需要更新的字段
    if report.report_json is None:
        report.report_json = {}

    # 标记 report_json 需要更新
    report_json_changed = False

    # 新模板字段列表
    new_template_fields = ['dtype', 'features', 'serve_cmd', 'environment', 'tasks']

    # 先处理 report_json 直接更新（如果前端直接传了完整的 report_json）
    if 'report_json' in update_data:
        report_json = update_data.pop('report_json')
        # 合并而不是覆盖，确保新值覆盖旧值
        report.report_json.update(report_json)
        report_json_changed = True
        logger.info(f"更新 report_json: {report.report_json}")

    # 处理 vllm_ascend_version 更新到 report_json（优先级更高，最后设置）
    if 'vllm_ascend_version' in update_data:
        vllm_ascend_version = update_data.pop('vllm_ascend_version')
        report.report_json['vllm_ascend_version'] = vllm_ascend_version
        report_json_changed = True
        logger.info(f"更新 report_json.vllm_ascend_version: {vllm_ascend_version}")

    # 处理 vllm_version 更新到 report_json（优先级更高，最后设置）
    if 'vllm_version' in update_data:
        vllm_version = update_data.pop('vllm_version')
        report.report_json['vllm_version'] = vllm_version
        report_json_changed = True
        logger.info(f"更新 report_json.vllm_version: {vllm_version}")

    # 处理新模板字段：同时更新数据库列和 report_json
    for field in new_template_fields:
        if field in update_data:
            value = update_data.pop(field)
            # 更新数据库列
            setattr(report, field, value)
            # 同步到 report_json
            if value is not None:
                report.report_json[field] = value
            elif field in report.report_json:
                # 如果值为 None，从 report_json 中删除
                del report.report_json[field]
            report_json_changed = True
            logger.info(f"更新字段 {field}: {value}")

    # 处理其他剩余字段
    for field, value in update_data.items():
        logger.info(f"更新字段 {field}: {value}")
        setattr(report, field, value)

    # 强制标记 report_json 为已修改（解决 JSON 字段修改检测问题）
    if report_json_changed:
        from sqlalchemy.orm import attributes
        attributes.flag_modified(report, 'report_json')
        logger.info("已标记 report_json 为 modified")

    await db.commit()
    await db.refresh(report)

    return report


@router.delete("/{model_id}/reports/{report_id}", response_model=Message)
async def delete_report(
    db: DbSession,
    model_id: int,
    report_id: int,
    current_user: CurrentAdminUser
):
    """删除报告"""
    import logging

    from sqlalchemy import delete as sqlalchemy_delete
    logger = logging.getLogger(__name__)
    logger.info(f"删除报告：model_id={model_id}, report_id={report_id}, user={current_user.username}")

    # 先查询报告是否存在
    stmt = select(ModelReport).where(
        ModelReport.id == report_id,
        ModelReport.model_config_id == model_id
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if not report:
        logger.warning(f"报告不存在：report_id={report_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="报告不存在",
        )

    logger.info(f"找到报告：id={report.id}, model_config_id={report.model_config_id}")

    # 使用 SQLAlchemy 2.0 的 delete() 语法
    delete_stmt = sqlalchemy_delete(ModelReport).where(ModelReport.id == report_id)
    await db.execute(delete_stmt)
    logger.info(f"已执行 DELETE 语句：report_id={report_id}")

    # 显式提交事务
    await db.commit()
    logger.info(f"事务已提交：report_id={report_id}")

    # 验证是否真的删除了
    verify_stmt = select(ModelReport).where(ModelReport.id == report_id)
    verify_result = await db.execute(verify_stmt)
    verify_report = verify_result.scalar_one_or_none()

    if verify_report:
        logger.error(f"删除失败！报告仍然存在：report_id={report_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除失败，请检查数据库权限或连接",
        )

    logger.info(f"验证删除成功：report_id={report_id}")

    return {"message": "报告已成功删除"}


# ============ 趋势分析 ============

@router.get("/{model_id}/trends", response_model=list[ModelTrendData])
async def get_model_trends(
    db: DbSession,
    model_id: int,
    days: int = Query(30, description="获取多少天的数据"),
    metric_keys: str | None = Query(None, description="需要提取的 metric keys，逗号分隔")
):
    """获取模型趋势数据"""
    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 解析 metric_keys
    keys = None
    if metric_keys:
        keys = [k.strip() for k in metric_keys.split(",")]

    # 获取趋势数据
    trend_service = ModelTrendService(db)
    trend_data = await trend_service.get_trend_data(model_id, days, keys)

    return trend_data


# ============ 启动命令 ============

@router.get("/{model_id}/startup-commands", response_model=dict[str, dict[str, dict[str, str]]])
async def get_startup_commands(db: DbSession, model_id: int):
    """获取模型的 vLLM 启动命令（多维度）"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 返回存储的启动命令
    if model.startup_commands:
        try:
            return json.loads(model.startup_commands)
        except Exception:
            # 如果不是 JSON，返回空字典
            return {}

    return {}


@router.put("/{model_id}/startup-commands", response_model=dict[str, dict[str, dict[str, str]]])
async def update_startup_commands(
    db: DbSession,
    model_id: int,
    commands: dict[str, dict[str, dict[str, str]]],
    current_user: CurrentAdminUser
):
    """更新启动命令（管理员可配置多维度命令）"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 保存为 JSON
    model.startup_commands = json.dumps(commands)
    await db.commit()
    await db.refresh(model)

    return commands


@router.post("/{model_id}/startup-commands/generate", response_model=dict[str, dict[str, dict[str, str]]])
async def generate_startup_command(
    db: DbSession,
    model_id: int,
    vllm_version: str | None = Query(None, description="vLLM 版本")
):
    """从 YAML 配置生成启动命令"""
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    if not model.config_yaml:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="模型没有配置 YAML",
        )

    generator = StartupCommandGenerator()
    command = generator.generate_from_yaml(model.config_yaml, vllm_version)

    # 返回多维度结构
    version = vllm_version or "default"
    return {
        version: {
            "standard": {
                "A2": command
            }
        }
    }


# ============ 手动上传报告 ============

@router.post("/{model_id}/reports/upload", response_model=ModelReportResponse, status_code=status.HTTP_201_CREATED)
async def upload_report(
    db: DbSession,
    model_id: int,
    file: UploadFile = File(...),
    vllm_version: str | None = Form(None),
    hardware: str | None = Form(None),
    current_user: CurrentAdminUser = None
):
    """手动上传模型报告（YAML 或 JSON 格式）"""
    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # 读取文件内容
    content = await file.read()
    content_str = content.decode("utf-8")

    # 判断文件类型
    filename = file.filename.lower()
    content_type = "json" if filename.endswith(".json") else "yaml"

    # 使用 ModelSyncService 处理上传
    sync_service = ModelSyncService(db, GitHubClient())

    try:
        report = await sync_service.create_report_from_upload(
            model_config_id=model_id,
            report_content=content_str,
            content_type=content_type,
            vllm_version=vllm_version,
            hardware=hardware
        )
    except ValueError as e:
        # 解析错误，返回友好的错误信息
        error_msg = str(e)
        if "Invalid JSON/YAML format" in error_msg:
            # 提取行列信息
            import re
            match = re.search(r'line (\d+) column (\d+)', error_msg)
            if match:
                line, col = match.groups()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"报告文件格式错误：在第 {line} 行第 {col} 列附近有语法问题。请检查 JSON/YAML 格式是否正确（注意逗号、引号、缩进）。"
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"报告文件解析失败：{error_msg}"
        )

    return report


# ============ 手动同步报告 ============

@router.post("/{model_id}/reports/sync", response_model=Message)
async def sync_reports(
    db: DbSession,
    model_id: int,
    current_user: CurrentAdminUser
):
    """手动触发报告同步（从 GitHub Actions artifacts）"""
    # 验证模型存在
    stmt = select(ModelConfig).where(ModelConfig.id == model_id)
    result = await db.execute(stmt)
    model = result.scalar_one_or_none()

    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型不存在",
        )

    # TODO: 实现从 GitHub 同步的逻辑
    # 目前返回成功消息，实际同步在后台任务中进行

    return {"message": "同步任务已触发，请稍后刷新查看结果"}


# ============ 支持矩阵 ============

async def get_support_matrix_endpoint_legacy(
    db: DbSession,
    model_type: str | None = Query(None),
    role: str | None = Query(None),
    series: str | None = Query(None),
    support_status: str | None = Query(None),
    tier: str | None = Query(None),
):
    """获取完整支持矩阵（模型×特性交叉表）"""
    from support_matrix.support_matrix_sync import get_support_matrix as _get_matrix

    return await _get_matrix(
        db,
        model_type=model_type,
        role=role,
        series=series,
        support_status=support_status,
        tier=tier,
    )


async def get_feature_compatibility_endpoint_legacy(db: DbSession):
    """获取 25×25 特性互操作矩阵"""
    from support_matrix.support_matrix_sync import get_feature_compatibility as _get_compat

    return await _get_compat(db)


async def get_global_features_endpoint_legacy(db: DbSession):
    """获取全局功能支持状态（来自 supported_features.md）"""
    from support_matrix.support_matrix_sync import get_global_features as _get_gf

    return await _get_gf(db)


async def get_sync_status_endpoint_legacy(db: DbSession):
    """获取最近同步状态"""
    from support_matrix.support_matrix_sync import get_sync_status as _get_ss

    return await _get_ss(db)


async def sync_upstream_endpoint_legacy(
    db: DbSession,
    current_user: CurrentAdminUser,
    dry_run: bool = Query(False),
):
    """手动触发上游支持矩阵同步（管理员）

    参数 dry_run=True 时只返回 diff 不落库
    """
    from support_matrix.support_matrix_sync import sync_support_matrix as _sync

    result = await _sync(db, dry_run=dry_run)
    return result
