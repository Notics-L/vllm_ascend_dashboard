"""
系统配置 API 路由
提供系统配置的查看和更新功能
"""
import logging
import re
from datetime import UTC
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import (
    get_current_active_admin_user,
    get_current_active_super_admin_user,
    get_current_user,
    get_db,
)
from infrastructure.core.config import settings
from infrastructure.core.email import SMTP_CONFIG_KEY
from infrastructure.persistence.models import ProjectDashboardConfig, User

logger = logging.getLogger(__name__)

router = APIRouter()


class GitHubConfigUpdateRequest(BaseModel):
    github_token: str | None = None
    clear_token: bool = False


@router.get("")
async def get_system_config(
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)]
):
    """
    获取系统配置

    需要超级管理员权限（super_admin）
    返回可公开的配置信息（敏感数据如 token 会脱敏显示）
    """
    # 时区配置从数据库读取，默认 Asia/Shanghai
    from sqlalchemy import select

    from infrastructure.core.app_runtime_config import load_app_runtime_config
    from infrastructure.core.github_config import load_github_runtime_config
    from infrastructure.tasks.scheduler_config import load_scheduler_runtime_config

    await load_app_runtime_config()
    await load_github_runtime_config()
    await load_scheduler_runtime_config()
    from infrastructure.db.base import SessionLocal
    from infrastructure.persistence.models import ProjectDashboardConfig

    timezone_str = 'Asia/Shanghai'  # 默认时区
    try:
        async with SessionLocal() as db:
            stmt = select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == 'daily_summary_schedule'
            )
            result = await db.execute(stmt)
            config = result.scalar_one_or_none()
            if config and 'timezone' in config.config_value:
                timezone_str = config.config_value['timezone']
    except Exception as e:
        logger.warning(f"Failed to load timezone from database, using default: {timezone_str}. Error: {e}")

    # 只返回非敏感配置
    return {
        "app_config": {
            "environment": settings.ENVIRONMENT,
            "debug": settings.DEBUG,
            "log_level": settings.LOG_LEVEL,
            "timezone": timezone_str,  # 从数据库读取
        },
        "github_config": {
            "owner": settings.GITHUB_OWNER,
            "repo": settings.GITHUB_REPO,
            "token_configured": bool(settings.GITHUB_TOKEN),
        },
        "sync_config": {
            "ci_sync_config": {
                "sync_interval_minutes": settings.CI_SYNC_INTERVAL_MINUTES,
                "days_back": settings.CI_SYNC_DAYS_BACK,
                "max_runs_per_workflow": settings.CI_SYNC_MAX_RUNS_PER_WORKFLOW,
                "force_full_refresh": settings.CI_SYNC_FORCE_FULL_REFRESH,
                "auto_failure_analysis_enabled": settings.CI_AUTO_FAILURE_ANALYSIS_ENABLED,
            },
            "model_sync_config": {
                "sync_interval_minutes": settings.MODEL_SYNC_INTERVAL_MINUTES,
                "days_back": settings.MODEL_SYNC_DAYS_BACK,
                "runs_limit": settings.MODEL_SYNC_RUNS_LIMIT,
            },
            "pr_pipeline_sync_config": {
                "sync_interval_minutes": settings.PR_PIPELINE_SYNC_INTERVAL_MINUTES,
                "days_back": settings.PR_PIPELINE_DAYS_BACK,
                "max_items_per_sync": settings.PR_PIPELINE_MAX_ITEMS_PER_SYNC,
                "incremental_lookback_minutes": settings.PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES,
            },
            "data_retention_days": settings.DATA_RETENTION_DAYS,
            "project_dashboard_cache_interval_minutes": settings.PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES,
            "github_cache_dir": settings.GITHUB_CACHE_DIR,
        },
        "database_config": {
            "type": "mysql",
            "configured": bool(settings.DATABASE_URL),
        },
    }


@router.put("/app")
async def update_app_config(
    log_level: str | None = Query(None),
    debug: bool | None = Query(None),
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    更新应用配置

    需要超级管理员权限（super_admin）
    更新 API 运行时配置并持久化到 MySQL 控制面
    """
    updates = []
    runtime_updates = {}

    if log_level is not None:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level.upper() not in valid_levels:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"日志级别必须是：{', '.join(valid_levels)}",
            )

        # 更新运行时配置
        old_level = settings.LOG_LEVEL
        settings.LOG_LEVEL = log_level.upper()
        runtime_updates['log_level'] = log_level.upper()
        updates.append(f"日志级别：{old_level} → {log_level.upper()}")

        # 动态调整 logging 级别（无需重启）
        try:
            new_level = getattr(logging, log_level.upper())
            root_logger = logging.getLogger()
            root_logger.setLevel(new_level)

            # 更新所有 handler 的级别
            for handler in root_logger.handlers:
                handler.setLevel(new_level)

            updates.append("✅ 日志级别已动态调整（无需重启）")
            logger.info(f"Log level dynamically changed from {old_level} to {log_level.upper()}")
        except Exception as e:
            logger.error(f"Failed to dynamically adjust log level: {e}")
            updates.append("⚠️ 日志级别已更新，但需要重启服务才能生效")

    if debug is not None:
        settings.DEBUG = debug
        runtime_updates['debug'] = debug
        updates.append(f"调试模式：{'开启' if debug else '关闭'}")

    if runtime_updates:
        from infrastructure.core.app_runtime_config import persist_app_runtime_config

        await persist_app_runtime_config(runtime_updates, db)
        updates.append("运行时配置已保存到 MySQL 控制面")
        if debug is not None:
            updates.append("Debug 启动行为将在服务重启后生效")

    return {
        "success": True,
        "message": "配置已更新：" + ", ".join(updates) if updates else "无更新",
        "updates": updates,
    }


@router.put("/github")
async def update_github_config(
    payload: GitHubConfigUpdateRequest | None = Body(default=None),
    github_token: str | None = Query(None),
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)] = None
):
    """
    更新 GitHub 配置

    需要超级管理员权限（super_admin）
    更新运行时配置并通过 MySQL/control_outbox 通知 Scheduler

    注意：GitHub 项目固定为 vllm-project/vllm-ascend，不可修改
    """
    from infrastructure.core.github_config import (
        clear_github_runtime_token,
        load_github_runtime_config,
        persist_github_runtime_config,
    )

    updates = []

    # ── 清除 token：回退到 .env 启动值 ──
    do_clear = getattr(payload, "clear_token", False) if payload else False
    if do_clear:
        removed = await clear_github_runtime_token()
        # 清除后重新加载：如果 .env 有 bootstrap token 且 DB 已无 token，
        # settings.GITHUB_TOKEN 保持当前 .env 值（不会被覆盖）
        await load_github_runtime_config()
        updates.append(
            "GitHub Token 已从系统配置清除（回退到 .env 配置）"
            if removed
            else "GitHub Token 已是清除状态"
        )

    requested_token = payload.github_token if payload else github_token
    if not do_clear and requested_token is not None and len(requested_token.strip()) > 0:
        # 验证 token 格式（GitHub token 通常以 ghp_ 或 github_pat_ 开头）
        token = requested_token.strip()
        if not (token.startswith("ghp_") or token.startswith("github_pat_")) and len(token) < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub Token 格式不正确，应以 ghp_ 或 github_pat_ 开头",
            )

        # Verify the credential before changing the process or control-plane
        # state.  A syntactically valid token can still be revoked or expired;
        # persisting it first would make every worker create a failing task.
        from infrastructure.clients.github_client import (
            GitHubAPIError,
            GitHubAuthenticationError,
            GitHubClient,
        )

        # 在 Docker 网络代理环境下，验证可能因 SSL 证书问题失败，
        # 此时回退到仅格式校验 + 直接保存（用户可自行确认 token 有效性）
        github_client = GitHubClient(token)
        try:
            await github_client.get_rate_limit_status()
            verified = True
        except GitHubAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub Token 已被拒绝或已失效，请更换有效 Token",
            ) from exc
        except GitHubAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"GitHub Token 验证失败：{exc}",
            ) from exc
        except Exception as exc:
            logger.warning("GitHub token pre-verification failed (non-API error): %s", exc)
            verified = False
        finally:
            await github_client.close()

        settings.GITHUB_TOKEN = token
        await persist_github_runtime_config(token=token)
        updates.append("GitHub Token 已更新" + ("（未验证）" if not verified else ""))

    return {
        "success": True,
        "message": "配置已更新：" + ", ".join(updates) if updates else "无更新",
        "updates": updates,
    }


@router.put("/sync")
async def update_sync_config(
    ci_sync_interval_minutes: int | None = Query(None),
    ci_sync_days_back: int | None = Query(None),
    ci_sync_max_runs_per_workflow: int | None = Query(None),
    ci_sync_force_full_refresh: bool | None = Query(None),
    ci_auto_failure_analysis_enabled: bool | None = Query(None),
    model_sync_interval_minutes: int | None = Query(None),
    model_sync_days_back: int | None = Query(None),
    model_sync_runs_limit: int | None = Query(None),
    pr_pipeline_sync_interval_minutes: int | None = Query(None),
    pr_pipeline_days_back: int | None = Query(None),
    pr_pipeline_max_items_per_sync: int | None = Query(None),
    pr_pipeline_incremental_lookback_minutes: int | None = Query(None),
    data_retention_days: int | None = Query(None),
    project_dashboard_cache_interval_minutes: int | None = Query(None),
    github_cache_dir: str | None = Query(None),
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)] = None
):
    """
    更新同步配置

    需要超级管理员权限（super_admin）
    更新运行时配置并通过 MySQL/control_outbox 通知 Scheduler
    """
    from infrastructure.tasks.scheduler_config import persist_scheduler_runtime_config

    updates = []
    runtime_updates = {}

    # 验证参数
    if ci_sync_interval_minutes is not None:
        if ci_sync_interval_minutes < 1 or ci_sync_interval_minutes > 10080:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="同步间隔必须在 1-10080 分钟之间",
            )

    if ci_sync_days_back is not None:
        if ci_sync_days_back < 1 or ci_sync_days_back > 90:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="同步天数必须在 1-90 天之间",
            )

    if ci_sync_max_runs_per_workflow is not None:
        if ci_sync_max_runs_per_workflow < 1 or ci_sync_max_runs_per_workflow > 1000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="每个 Workflow 最多采集数量必须在 1-1000 之间",
            )

    if model_sync_interval_minutes is not None:
        if model_sync_interval_minutes < 1 or model_sync_interval_minutes > 10080:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="模型同步间隔必须在 1-10080 分钟之间",
            )

    if model_sync_days_back is not None:
        if model_sync_days_back < 1 or model_sync_days_back > 90:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="模型同步天数必须在 1-90 天之间",
            )

    if model_sync_runs_limit is not None:
        if model_sync_runs_limit < 1 or model_sync_runs_limit > 1000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="每个 Workflow 最多获取 Runs 数量必须在 1-1000 之间",
            )

    if pr_pipeline_sync_interval_minutes is not None and not 1 <= pr_pipeline_sync_interval_minutes <= 10080:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 同步间隔必须在 1-10080 分钟之间")
    if pr_pipeline_days_back is not None and not 1 <= pr_pipeline_days_back <= 90:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 首次回溯天数必须在 1-90 天之间")
    if pr_pipeline_max_items_per_sync is not None and not 1 <= pr_pipeline_max_items_per_sync <= 1000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 单次最多同步数量必须在 1-1000 之间")
    if pr_pipeline_incremental_lookback_minutes is not None and not 0 <= pr_pipeline_incremental_lookback_minutes <= 1440:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PR 增量重叠窗口必须在 0-1440 分钟之间")

    if data_retention_days is not None:
        if data_retention_days < 1 or data_retention_days > 3650:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="数据保留天数必须在 1-3650 天之间",
            )

    if project_dashboard_cache_interval_minutes is not None:
        if project_dashboard_cache_interval_minutes != 1440:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git 仓库缓存固定每天更新一次（1440 分钟）",
            )

    if github_cache_dir is not None:
        settings.GITHUB_CACHE_DIR = github_cache_dir
        runtime_updates['github_cache_dir'] = github_cache_dir
        updates.append(f"GitHub 缓存目录：{github_cache_dir or '默认 (data/)'}")

    # 更新配置（运行时）
    if ci_sync_interval_minutes is not None:
        settings.CI_SYNC_INTERVAL_MINUTES = ci_sync_interval_minutes
        runtime_updates['ci_sync_interval_minutes'] = ci_sync_interval_minutes
        updates.append(f"CI 同步间隔：{ci_sync_interval_minutes}分钟")

    if ci_sync_days_back is not None:
        settings.CI_SYNC_DAYS_BACK = ci_sync_days_back
        runtime_updates['ci_sync_days_back'] = ci_sync_days_back
        updates.append(f"同步天数范围：{ci_sync_days_back}天")

    if ci_sync_max_runs_per_workflow is not None:
        settings.CI_SYNC_MAX_RUNS_PER_WORKFLOW = ci_sync_max_runs_per_workflow
        runtime_updates['ci_sync_max_runs_per_workflow'] = ci_sync_max_runs_per_workflow
        updates.append(f"每个 Workflow 最多采集：{ci_sync_max_runs_per_workflow}条")

    if ci_sync_force_full_refresh is not None:
        settings.CI_SYNC_FORCE_FULL_REFRESH = ci_sync_force_full_refresh
        runtime_updates['ci_sync_force_full_refresh'] = ci_sync_force_full_refresh
        updates.append(f"全量覆盖刷新：{'开启' if ci_sync_force_full_refresh else '关闭'}")

    if ci_auto_failure_analysis_enabled is not None:
        settings.CI_AUTO_FAILURE_ANALYSIS_ENABLED = ci_auto_failure_analysis_enabled
        runtime_updates['ci_auto_failure_analysis_enabled'] = ci_auto_failure_analysis_enabled
        updates.append(
            f"同步后自动失败分析：{'开启' if ci_auto_failure_analysis_enabled else '关闭'}"
        )

    if data_retention_days is not None:
        settings.DATA_RETENTION_DAYS = data_retention_days
        runtime_updates['data_retention_days'] = data_retention_days
        updates.append(f"数据保留天数：{data_retention_days}天")

    if project_dashboard_cache_interval_minutes is not None:
        settings.PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES = project_dashboard_cache_interval_minutes
        runtime_updates['project_dashboard_cache_interval_minutes'] = project_dashboard_cache_interval_minutes
        updates.append(f"Project Dashboard 缓存更新间隔：{project_dashboard_cache_interval_minutes}分钟")

    # 模型同步配置更新
    if model_sync_interval_minutes is not None:
        settings.MODEL_SYNC_INTERVAL_MINUTES = model_sync_interval_minutes
        runtime_updates['model_sync_interval_minutes'] = model_sync_interval_minutes
        updates.append(f"模型同步间隔：{model_sync_interval_minutes}分钟")

    if model_sync_days_back is not None:
        settings.MODEL_SYNC_DAYS_BACK = model_sync_days_back
        runtime_updates['model_sync_days_back'] = model_sync_days_back
        updates.append(f"模型同步天数范围：{model_sync_days_back}天")

    if model_sync_runs_limit is not None:
        settings.MODEL_SYNC_RUNS_LIMIT = model_sync_runs_limit
        runtime_updates['model_sync_runs_limit'] = model_sync_runs_limit
        updates.append(f"每个 Workflow 最多获取 Runs: {model_sync_runs_limit}条")

    if pr_pipeline_sync_interval_minutes is not None:
        settings.PR_PIPELINE_SYNC_INTERVAL_MINUTES = pr_pipeline_sync_interval_minutes
        runtime_updates['pr_pipeline_sync_interval_minutes'] = pr_pipeline_sync_interval_minutes
        updates.append(f"PR 同步间隔：{pr_pipeline_sync_interval_minutes}分钟")

    if pr_pipeline_days_back is not None:
        settings.PR_PIPELINE_DAYS_BACK = pr_pipeline_days_back
        runtime_updates['pr_pipeline_days_back'] = pr_pipeline_days_back
        updates.append(f"PR 首次回溯：{pr_pipeline_days_back}天")

    if pr_pipeline_max_items_per_sync is not None:
        settings.PR_PIPELINE_MAX_ITEMS_PER_SYNC = pr_pipeline_max_items_per_sync
        runtime_updates['pr_pipeline_max_items_per_sync'] = pr_pipeline_max_items_per_sync
        updates.append(f"PR 单次最多同步：{pr_pipeline_max_items_per_sync}条")

    if pr_pipeline_incremental_lookback_minutes is not None:
        settings.PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES = pr_pipeline_incremental_lookback_minutes
        runtime_updates['pr_pipeline_incremental_lookback_minutes'] = pr_pipeline_incremental_lookback_minutes
        updates.append(f"PR 增量重叠窗口：{pr_pipeline_incremental_lookback_minutes}分钟")

    # Persist to the MySQL control plane and notify Scheduler.
    await persist_scheduler_runtime_config(runtime_updates)
    if runtime_updates:
        updates.append("运行时配置已保存到 MySQL 控制面")
    updates.append("scheduler configuration reload queued")

    return {
        "success": True,
        "message": "配置已更新：" + ", ".join(updates) if updates else "无更新",
        "updates": updates,
    }


@router.post("/sync/trigger")
async def trigger_sync_config(
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)] = None
):
    """
    手动触发配置重载

    需要超级管理员权限（super_admin）
    从 MySQL 控制面重新加载配置
    """

    try:
        from infrastructure.tasks.scheduler_config import (
            load_scheduler_runtime_config,
            persist_scheduler_runtime_config,
        )

        # Reload the durable control-plane values into this API process.  The
        # Scheduler receives its own reload through control_outbox.
        await load_scheduler_runtime_config()
        await persist_scheduler_runtime_config(
            {"ci_sync_interval_minutes": settings.CI_SYNC_INTERVAL_MINUTES}
        )
        return {
            "success": True,
            "message": "已从 MySQL 控制面重新加载配置；Scheduler 通过 control_outbox 自动刷新",
            "current_config": {
                "ci_sync_interval_minutes": settings.CI_SYNC_INTERVAL_MINUTES,
                "data_retention_days": settings.DATA_RETENTION_DAYS,
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"配置重载失败：{str(e)}",
        )


@router.get("/status")
async def get_system_status(
    current_user: Annotated[User, Depends(get_current_user)] = None
):
    """
    获取系统状态信息（所有登录用户可访问）
    """
    from datetime import datetime, timedelta

    from sqlalchemy import func, select, text

    from infrastructure.db.base import SessionLocal
    from infrastructure.persistence.models import SchedulerHeartbeat, WorkflowConfig

    # 一次 DB 读取：调度器心跳 + 最近同步时间
    async with SessionLocal() as db:
        try:
            heartbeat = await db.get(SchedulerHeartbeat, 1)
        except Exception as e:
            logger.warning(f"Failed to read scheduler heartbeat: {e}")
            heartbeat = None
        try:
            stmt = select(func.max(WorkflowConfig.last_sync_at)).where(WorkflowConfig.enabled)
            result = await db.execute(stmt)
            last_sync = result.scalar()
        except Exception as e:
            logger.warning(f"Failed to get last sync time: {e}")
            last_sync = None
        try:
            collector_rows = (
                await db.execute(
                    text(
                        "SELECT node_id, capabilities, running, active_tasks, updated_at "
                        "FROM collector_heartbeats"
                    )
                )
            ).mappings().all()
        except Exception as e:
            # The table is introduced by the process-runtime migration.  Keep
            # status available during a rolling upgrade before every node has run it.
            logger.warning(f"Failed to read collector heartbeats: {e}")
            collector_rows = []

    def _to_iso(v):
        """datetime 或已是 ISO 字符串统一转为 ISO 字符串。"""
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)

    # 心跳新鲜度阈值：心跳间隔 20s，90s ≈ 4 次未刷新即判定调度器已停止
    STALE_THRESHOLD = timedelta(seconds=90)
    now = datetime.now(UTC)

    active_collectors = []
    for row in collector_rows:
        updated = row["updated_at"]
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        is_healthy = bool(row["running"]) and updated is not None and (now - updated) < STALE_THRESHOLD
        active_collectors.append({
            "node_id": row["node_id"],
            "running": is_healthy,
            "active_tasks": row["active_tasks"],
            "updated_at": _to_iso(row["updated_at"]),
        })

    if heartbeat is not None and heartbeat.updated_at is not None:
        # 独立 scheduler 进程模式：用心跳判断存活（API 进程内 APScheduler 为空）
        updated = heartbeat.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        running = (now - updated) < STALE_THRESHOLD and bool(heartbeat.running)
        jobs_data = heartbeat.jobs or {}
        # 调度器未运行时 next_sync 无意义，置空让前端显示"-"
        hb_ci = jobs_data.get("ci_data_sync", {}).get("next_run") if running else None
        hb_model = jobs_data.get("model_report_sync", {}).get("next_run") if running else None
        hb_cache = jobs_data.get("project_dashboard_cache_update", {}).get("next_run") if running else None
        hb_daily = jobs_data.get("daily_summary_task", {}).get("next_run") if running else None
        ci_next_sync = _to_iso(hb_ci)
        model_next_sync = _to_iso(hb_model)
        cache_next_sync = _to_iso(hb_cache)
        daily_summary_next_sync = _to_iso(hb_daily)
    else:
        # Scheduler 是独立进程；没有持久化心跳即表示 API 无法确认其存活。
        running = False
        ci_next_sync = model_next_sync = cache_next_sync = daily_summary_next_sync = None

    return {
        "scheduler": {
            "running": running,
            "sync_interval_minutes": settings.CI_SYNC_INTERVAL_MINUTES,
            "last_sync": _to_iso(last_sync),  # 上次同步时间
            "tasks": {
                "ci_sync": {
                    "name": "CI 数据同步",
                    "next_sync": ci_next_sync,
                    "interval_minutes": settings.CI_SYNC_INTERVAL_MINUTES,
                },
                "model_report_sync": {
                    "name": "模型报告同步",
                    "next_sync": model_next_sync,
                    "interval_minutes": settings.MODEL_SYNC_INTERVAL_MINUTES,
                },
                "project_dashboard_cache": {
                    "name": "Git 仓库缓存更新",
                    "next_sync": cache_next_sync,
                    "interval_minutes": settings.PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES,
                },
                "daily_summary": {
                    "name": "项目动态同步",
                    "next_sync": daily_summary_next_sync,
                    "enabled": getattr(settings, 'DAILY_SUMMARY_ENABLED', True),
                    "cron_hour": getattr(settings, 'DAILY_SUMMARY_CRON_HOUR', 8),
                    "cron_minute": getattr(settings, 'DAILY_SUMMARY_CRON_MINUTE', 0),
                },
            },
        },
        "collectors": {
            "active_count": sum(1 for collector in active_collectors if collector["running"]),
            "nodes": active_collectors,
        },
        "database": {
            "connected": True,  # 如果能响应说明数据库连接正常
            "type": "mysql",
        },
        "github": {
            "configured": bool(settings.GITHUB_TOKEN),
            "owner": settings.GITHUB_OWNER,
            "repo": settings.GITHUB_REPO,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/git-cache/status")
async def get_git_cache_status(
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)]
):
    """
    获取 Git 缓存状态

    需要超级管理员权限（super_admin）
    返回最新 commit 信息，方便管理员判断是否需要手动同步
    支持多个仓库：vllm-ascend 和 vllm
    """
    from infrastructure.clients.github_cache import get_github_cache, get_github_cache_for_repo

    # 获取默认仓库（vllm-ascend）的缓存状态
    ascend_cache = get_github_cache()
    ascend_commit = ascend_cache.get_latest_commit() if ascend_cache._is_repo_cloned() else None

    # 获取 vllm 仓库的缓存状态
    vllm_cache = get_github_cache_for_repo(owner="vllm-project", repo="vllm")
    vllm_commit = vllm_cache.get_latest_commit() if vllm_cache._is_repo_cloned() else None

    return {
        "repositories": {
            "ascend": {
                "owner": ascend_cache.owner,
                "repo": ascend_cache.repo,
                "latest_commit": ascend_commit,
                "cache_dir": str(ascend_cache.cache_dir),
                "mirror_dir": str(ascend_cache.mirror.mirror_dir) if ascend_cache.mirror else None,
                "is_cloned": ascend_cache._is_repo_cloned(),
            },
            "vllm": {
                "owner": vllm_cache.owner,
                "repo": vllm_cache.repo,
                "latest_commit": vllm_commit,
                "cache_dir": str(vllm_cache.cache_dir),
                "mirror_dir": str(vllm_cache.mirror.mirror_dir) if vllm_cache.mirror else None,
                "is_cloned": vllm_cache._is_repo_cloned(),
            },
        },
        # 保持向后兼容
        "latest_commit": ascend_commit,
        "cache_dir": str(ascend_cache.cache_dir),
        "is_cloned": ascend_cache._is_repo_cloned(),
    }


@router.post("/git-cache/sync")
async def sync_git_cache(
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    repo_type: str = Query("all", description="仓库类型: ascend, vllm, all"),
    db: AsyncSession = Depends(get_db),
):
    """Queue a mirror refresh; API workers never perform Git/network I/O."""
    if repo_type not in {"ascend", "vllm", "all"}:
        raise HTTPException(status_code=400, detail="repo_type 必须是 ascend、vllm 或 all")

    from infrastructure.tasks.task_manager import TaskManager

    node_ids = (
        await db.execute(
            text(
                """
                SELECT node_id FROM collector_heartbeats
                WHERE running = 1 AND updated_at >= NOW() - INTERVAL 2 MINUTE
                """
            )
        )
    ).scalars().all()
    targets = sorted({str(node_id) for node_id in node_ids if node_id}) or [""]
    task_ids = []
    request_id = str(uuid4())
    for node_id in targets:
        task_id = await TaskManager.create_task(
            db,
            "repo_cache_refresh",
            {"repo_type": repo_type, "target_node": node_id or None},
            f"repo_cache_refresh:manual:{repo_type}:{request_id}:{node_id or 'fallback'}",
            required_capability=f"node:{node_id}" if node_id else "python",
            priority=20,
        )
        if task_id:
            task_ids.append(task_id)
    await db.commit()
    return {
        "success": True,
        "status": "queued",
        "task_id": task_ids[0] if task_ids else None,
        "task_ids": task_ids,
        "repo_type": repo_type,
        "results": [
            {
                "repo": repo_type,
                "action": "queued",
                "success": True,
                "message": "镜像更新任务已进入 Collector 队列",
            }
        ],
    }


# ============ 每日总结配置相关 API ============

@router.get("/daily-summary")
async def get_daily_summary_config(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db)
):
    """
    获取每日总结配置

    所有登录用户可访问
    """
    from infrastructure.persistence.models import ProjectDashboardConfig
    from infrastructure.persistence.models.daily_summary import LLMProviderConfig

    try:
        # 获取定时任务配置
        schedule_stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == 'daily_summary_schedule'
        )
        schedule_result = await db.execute(schedule_stmt)
        schedule_config = schedule_result.scalar_one_or_none()

        # 获取项目配置
        projects_stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == 'daily_summary_projects'
        )
        projects_result = await db.execute(projects_stmt)
        projects_config = projects_result.scalar_one_or_none()

        # 获取 LLM 提供商列表
        llm_stmt = select(LLMProviderConfig).order_by(LLMProviderConfig.display_order)
        llm_result = await db.execute(llm_stmt)
        llm_providers = llm_result.scalars().all()

        # 检查 API Key 是否配置
        llm_list = []
        for llm in llm_providers:
            llm_list.append({
                "provider": llm.provider,
                "display_name": llm.display_name,
                "default_model": llm.default_model,
                "enabled": llm.enabled,
                "is_active": llm.is_active,
                "display_order": llm.display_order,
                "api_key_configured": bool(llm.api_key),
            })

        return {
            "enabled": schedule_config.config_value.get('enabled', True) if schedule_config else True,
            "cron_hour": schedule_config.config_value.get('cron_hour', 8) if schedule_config else 8,
            "cron_minute": schedule_config.config_value.get('cron_minute', 0) if schedule_config else 0,
            "timezone": schedule_config.config_value.get('timezone', 'Asia/Shanghai') if schedule_config else 'Asia/Shanghai',
            "llm_providers": llm_list,
            "projects": projects_config.config_value if projects_config else [
                {"id": "ascend", "name": "vLLM Ascend", "enabled": True},
                {"id": "vllm", "name": "vLLM", "enabled": True},
            ],
        }
    except Exception as e:
        logger.error(f"Failed to get daily summary config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取配置失败：{str(e)}"
        )


@router.put("/daily-summary")
async def update_daily_summary_config(
    config: dict,
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    db: AsyncSession = Depends(get_db)
):
    """
    更新每日总结配置

    需要管理员权限
    """
    from infrastructure.persistence.models import ProjectDashboardConfig

    try:
        # 更新定时任务配置
        if 'enabled' in config or 'cron_hour' in config or 'cron_minute' in config or 'timezone' in config:
            schedule_stmt = select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == 'daily_summary_schedule'
            )
            schedule_result = await db.execute(schedule_stmt)
            schedule_config = schedule_result.scalar_one_or_none()

            if schedule_config:
                schedule_config.config_value.update({
                    'enabled': config.get('enabled', True),
                    'cron_hour': config.get('cron_hour', 8),
                    'cron_minute': config.get('cron_minute', 0),
                    'timezone': config.get('timezone', 'Asia/Shanghai'),
                })
            else:
                schedule_config = ProjectDashboardConfig(
                    config_key='daily_summary_schedule',
                    config_value={
                        'enabled': config.get('enabled', True),
                        'cron_hour': config.get('cron_hour', 8),
                        'cron_minute': config.get('cron_minute', 0),
                        'timezone': config.get('timezone', 'Asia/Shanghai'),
                    },
                    description='每日总结定时任务配置',
                )
                db.add(schedule_config)

        # 更新项目配置
        if 'projects' in config:
            projects_stmt = select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == 'daily_summary_projects'
            )
            projects_result = await db.execute(projects_stmt)
            projects_config = projects_result.scalar_one_or_none()

            if projects_config:
                projects_config.config_value = config['projects']
            else:
                projects_config = ProjectDashboardConfig(
                    config_key='daily_summary_projects',
                    config_value=config['projects'],
                    description='每日总结项目配置',
                )
                db.add(projects_config)

        await db.commit()

        return {
            "success": True,
            "message": "配置已更新",
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update daily summary config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新配置失败：{str(e)}"
        )


@router.get("/llm-providers")
async def get_llm_providers(
    current_user: Annotated[User, Depends(get_current_active_admin_user)],
    db: AsyncSession = Depends(get_db)
):
    """
    获取 LLM 提供商列表

    需要管理员权限
    """
    from infrastructure.persistence.models.daily_summary import LLMProviderConfig
    try:
        stmt = select(LLMProviderConfig).order_by(LLMProviderConfig.display_order)
        result = await db.execute(stmt)
        providers = result.scalars().all()

        return [
            {
                "provider": p.provider,
                "display_name": p.display_name,
                "default_model": p.default_model,
                "enabled": p.enabled,
                "is_active": p.is_active,
                "display_order": p.display_order,
                "api_key_configured": bool(p.api_key),
                "api_base_url": p.api_base_url,
            }
            for p in providers
        ]
    except Exception as e:
        logger.error(f"Failed to get LLM providers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取 LLM 提供商失败：{str(e)}"
        )


@router.put("/llm-providers/{provider}")
async def update_llm_provider(
    provider: str,
    config: dict,
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    db: AsyncSession = Depends(get_db)
):
    """
    更新 LLM 提供商配置

    需要管理员权限

    可更新字段：
    - enabled: 是否启用
    - is_active: 是否为当前激活的提供商（用于 AI 总结）
    - default_model: 默认模型
    - display_name: 显示名称
    - api_key: API Key
    - api_base_url: API 基础 URL
    """
    from infrastructure.persistence.models.daily_summary import LLMProviderConfig

    try:
        stmt = select(LLMProviderConfig).where(LLMProviderConfig.provider == provider)
        result = await db.execute(stmt)
        provider_config = result.scalar_one_or_none()

        if not provider_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"LLM 提供商 {provider} 不存在"
            )

        was_active = provider_config.is_active

        # 更新字段
        if 'enabled' in config:
            provider_config.enabled = config['enabled']

        if 'is_active' in config:
            # 如果设置为激活，需要先取消其他所有 provider 的激活状态
            if config['is_active']:
                deactivate_stmt = select(LLMProviderConfig).where(
                    LLMProviderConfig.is_active
                )
                deactivate_result = await db.execute(deactivate_stmt)
                active_providers = deactivate_result.scalars().all()
                for active_p in active_providers:
                    active_p.is_active = False
            provider_config.is_active = config['is_active']

        if 'default_model' in config:
            provider_config.default_model = config['default_model']

        if 'display_name' in config:
            provider_config.display_name = config['display_name']

        if 'api_key' in config:
            from infrastructure.core.security import encrypt_api_key
            provider_config.api_key = encrypt_api_key(config['api_key'])

        if 'api_base_url' in config:
            provider_config.api_base_url = config['api_base_url']

        # Active-provider changes must reach the live LiteLLM router before
        # they are acknowledged.  The older flow committed first and only
        # logged a failed hot reload, leaving the UI/DB on a model that the
        # gateway could not route.
        # Deactivating the current provider also changes the generated
        # runtime file, so include its state *before* applying the request.
        should_sync = was_active or provider_config.is_active
        if should_sync:
            await db.flush()
            from model_sync.litellm_sync import get_litellm_sync
            sync = get_litellm_sync()
            if sync.available:
                await sync.sync_from_db(db)

        await db.commit()

        return {
            "success": True,
            "message": f"LLM 提供商 {provider} 配置已更新",
            "data": {
                "provider": provider_config.provider,
                "is_active": provider_config.is_active,
                "api_key_configured": bool(provider_config.api_key),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        from model_sync.litellm_sync import LiteLLMConfigSyncError
        if isinstance(e, LiteLLMConfigSyncError):
            await db.rollback()
            logger.warning("LiteLLM provider update was not applied: %s", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"LiteLLM 配置尚未生效，未保存本次修改：{e}",
            ) from e
        await db.rollback()
        logger.error(f"Failed to update LLM provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新 LLM 提供商失败：{str(e)}"
        )


@router.post("/llm-providers", status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
    config: dict,
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    db: AsyncSession = Depends(get_db)
):
    """创建新的 LLM 提供商（需 super_admin）"""
    from infrastructure.core.security import encrypt_api_key
    from infrastructure.persistence.models.daily_summary import LLMProviderConfig

    provider_name = config.get("provider", "").strip()
    if not provider_name:
        raise HTTPException(status_code=400, detail="provider name is required")
    if not re.match(r'^[a-zA-Z0-9_-]+$', provider_name):
        raise HTTPException(status_code=400, detail="provider name 只能包含字母、数字、下划线和连字符")

    stmt = select(LLMProviderConfig).where(LLMProviderConfig.provider == provider_name)
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"LLM 提供商 {provider_name} 已存在")

    new_config = LLMProviderConfig(
        provider=provider_name,
        display_name=config.get("display_name", provider_name),
        api_key=encrypt_api_key(config.get("api_key", "")) if config.get("api_key") else "",
        api_base_url=config.get("api_base_url", ""),
        default_model=config.get("default_model", ""),
        enabled=config.get("enabled", True),
        is_active=config.get("is_active", False),
        display_order=config.get("display_order", 999),
    )

    if new_config.is_active:
        deactivate_stmt = select(LLMProviderConfig).where(LLMProviderConfig.is_active)
        deactivate_result = await db.execute(deactivate_stmt)
        for active_p in deactivate_result.scalars().all():
            active_p.is_active = False

    db.add(new_config)
    if new_config.is_active:
        await db.flush()
        from model_sync.litellm_sync import get_litellm_sync
        sync = get_litellm_sync()
        if sync.available:
            await sync.sync_from_db(db)

    await db.commit()
    await db.refresh(new_config)

    logger.info(f"LLM provider created: {provider_name} by {current_user.username}")
    return {"success": True, "message": f"LLM 提供商 {provider_name} 已创建", "provider": provider_name}


@router.delete("/llm-providers/{provider}")
async def delete_llm_provider(
    provider: str,
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    db: AsyncSession = Depends(get_db)
):
    """删除 LLM 提供商（需 super_admin）"""
    from infrastructure.persistence.models.daily_summary import LLMProviderConfig

    stmt = select(LLMProviderConfig).where(LLMProviderConfig.provider == provider)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail=f"LLM 提供商 {provider} 不存在")

    if config.is_active:
        raise HTTPException(status_code=400, detail="不能删除当前激活的提供商，请先切换激活状态")

    await db.delete(config)
    await db.commit()

    logger.info(f"LLM provider deleted: {provider} by {current_user.username}")
    return {"success": True, "message": f"LLM 提供商 {provider} 已删除"}


# ============ 系统提示词配置 API ============


def get_system_prompt_scope_config(scope: str) -> tuple[str, dict[str, str], str]:
    if scope == "ci_failure_analysis":
        from agent.skill_registry import get_skill_registry
        registry = get_skill_registry()
        skill = registry.get_skill_by_scope("ci_failure_analysis")
        default_prompt = skill.content if skill and skill.content else "你是一名专业的 CI/CD 失败诊断分析师。请根据提供的 CI Job 失败信息，进行根因分析并给出改进建议。"
        return (
            "ci_failure_analysis_system_prompt",
            {"default": default_prompt},
            "系统提示词用于指导 AI 分析 CI 失败 Job 的根因和分类（基于 auto-bug-fixer 技能方法论 + CI 专属分类附录）",
        )
    if scope == "commit_analysis":
        return (
            "commit_analysis_system_prompt",
            {
                "ascend": """你是一名专业的 vLLM Ascend Commit 技术分析师。请基于给定的 Commit 元信息、关联 PR 信息、文件变更和已有人工分析字段，生成中文 Markdown 总结。

要求：
1. 概括该 Commit 做了什么
2. 判断修改类型：Feature / Bugfix / Refactor / Common / Test / CI / Other
3. 判断是否影响 API，并说明原因
4. 分析对 vLLM Ascend 的潜在影响；如不涉及，请明确说明“不涉及”
5. 给出后续跟进建议；如无需跟进，请明确说明“不涉及”
6. 输出结构清晰，避免编造未提供的信息""",
                "vllm": """你是一名专业的 vLLM Commit 技术分析师。请基于给定的 Commit 元信息、关联 PR 信息、文件变更和已有人工分析字段，生成中文 Markdown 总结。

要求：
1. 概括该 Commit 做了什么
2. 判断修改类型：Feature / Bugfix / Refactor / Common / Test / CI / Other
3. 判断是否影响 API，并说明原因
4. 分析对 vLLM Ascend 的潜在影响；如不涉及，请明确说明“不涉及”
5. 给出后续跟进建议；如无需跟进，请明确说明“不涉及”
6. 输出结构清晰，避免编造未提供的信息""",
            },
            "系统提示词用于指导 AI 生成单个 Commit 分析总结的风格和内容重点",
        )
    if scope == "daily_report":
        from agent.skill_registry import get_skill_registry
        registry = get_skill_registry()
        skill = registry.get_skill_by_scope("daily_report")
        default_prompt = skill.content if skill and skill.content else "你是一名 vLLM Ascend 社区运营分析师。请根据提供的全量看板数据，生成结构化每日运行报告。报告应包含：一句话总结、整体健康度、Nightly 流水线概况、PR 流水线概况、项目动态、风险与待办。每段先结论后数据，空数据板块整段省略。"
        return (
            "daily_report_system_prompt",
            {"default": default_prompt},
            "系统提示词用于指导 AI 生成每日运行报告（基于 daily-report-writer 技能）",
        )
    if scope != "daily_summary":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid system prompt scope: {scope}")
    return (
        "daily_summary_system_prompt",
        {
            "ascend": """你是一名专业的 vLLM Ascend 项目技术分析师。请根据以下数据生成项目动态总结和分析。

要求：
1. 总结 PR 趋势，包括热门修改领域、重要 PR 概述
2. 分析 Issue 热点，包括问题类型分布、用户反馈热点、需要关注的问题
3. 分析 Commit 活跃度，包括提交频率、代码变更热点
4. 综合以上信息，生成项目整体动态总结，包括：
   - 项目活跃度评估
   - 重要更新和里程碑
   - 需要关注的风险或问题
5. 使用 Markdown 格式，语言为中文
6. 结构清晰，重点突出，便于快速了解当日技术动态
7. 重点PR/ISSUE/COMMIT附带ID和github链接""",
            "vllm": """你是一名专业的 vLLM 项目分析师。请根据以下数据生成项目动态总结和分析。

要求：
1. 总结 PR 趋势，包括热门修改领域、重要 PR 概述
2. 分析 Issue 热点，包括问题类型分布、用户反馈热点、需要关注的问题
3. 分析 Commit 活跃度，包括提交频率、代码变更热点
4. 综合以上信息，生成项目整体动态总结，包括：
   - 项目活跃度评估
   - 重要更新和里程碑
   - 需要关注的风险或问题
5. 使用 Markdown 格式，语言为中文
6. 结构清晰，重点突出，便于快速了解当日技术动态
7. 重点PR/ISSUE/COMMIT附带ID和github链接""",
        },
        "系统提示词用于指导 AI 生成项目动态总结的风格和内容重点",
    )


@router.get("/system-prompt")
async def get_system_prompt_config(
    current_user: Annotated[User, Depends(get_current_user)],
    scope: str = "daily_summary",
    db: AsyncSession = Depends(get_db)
):
    """
    获取系统提示词配置

    所有登录用户可访问

    返回各项目的系统提示词配置：
    - ascend: vLLM Ascend 项目提示词
    - vllm: vLLM 项目提示词
    """
    from infrastructure.persistence.models import ProjectDashboardConfig

    try:
        config_key, default_prompts, default_description = get_system_prompt_scope_config(scope)
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == config_key
        )
        result = await db.execute(stmt)
        config = result.scalar_one_or_none()

        if config and config.config_value:
            return {
                "prompts": config.config_value,
                "description": config.description,
            }

        return {
            "prompts": default_prompts,
            "description": default_description,
        }
    except Exception as e:
        logger.error(f"Failed to get system prompt config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取系统提示词配置失败：{str(e)}"
        )


@router.put("/system-prompt")
async def update_system_prompt_config(
    config: dict,
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    scope: str = "daily_summary",
    db: AsyncSession = Depends(get_db)
):
    """
    更新系统提示词配置

    需要管理员权限

    请求体示例：
    {
        "prompts": {
            "ascend": "自定义的 ascend 项目提示词...",
            "vllm": "自定义的 vllm 项目提示词..."
        }
    }
    """
    from infrastructure.persistence.models import ProjectDashboardConfig

    try:
        if 'prompts' not in config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请提供 prompts 配置"
            )

        config_key, _, default_description = get_system_prompt_scope_config(scope)
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == config_key
        )
        result = await db.execute(stmt)
        prompt_config = result.scalar_one_or_none()

        if prompt_config:
            prompt_config.config_value = config['prompts']
            prompt_config.description = default_description
        else:
            prompt_config = ProjectDashboardConfig(
                config_key=config_key,
                config_value=config['prompts'],
                description=default_description,
            )
            db.add(prompt_config)

        await db.commit()

        return {
            "success": True,
            "message": "系统提示词配置已更新",
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update system prompt config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新系统提示词配置失败：{str(e)}"
        )


# ============ Skill 管理 API ============


@router.get("/skills")
async def list_skills(
    current_user: Annotated[User, Depends(get_current_active_admin_user)],
):
    from agent.skill_registry import get_skill_registry
    registry = get_skill_registry()
    skills = registry.list_skills()
    return [
        {
            "name": s.name,
            "description": s.description,
            "scope": s.scope,
            "file_path": s.file_path,
            "loaded_at": s.loaded_at,
            "content_length": len(s.content),
        }
        for s in skills
    ]


@router.post("/skills/refresh")
async def refresh_skills(
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
):
    from agent.skill_registry import refresh_skill_registry
    count = refresh_skill_registry()
    return {
        "success": True,
        "message": f"Skills refreshed, {count} skills loaded",
        "count": count,
    }


@router.get("/skills/{skill_name}")
async def get_skill_detail(
    skill_name: str,
    current_user: Annotated[User, Depends(get_current_active_admin_user)],
):
    from agent.skill_registry import get_skill_registry
    registry = get_skill_registry()
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_name}' not found",
        )
    return {
        "name": skill.name,
        "description": skill.description,
        "scope": skill.scope,
        "content": skill.content,
        "file_path": skill.file_path,
        "loaded_at": skill.loaded_at,
    }


# ── SMTP 配置（独立于每日报告）──

@router.get("/smtp")
async def get_smtp_config_endpoint(
    current_user: Annotated[User, Depends(get_current_active_admin_user)],
    db: AsyncSession = Depends(get_db),
):
    """获取 SMTP 邮件外发服务器配置（admin 权限）"""
    from infrastructure.core.email import get_smtp_config as _read
    config = await _read(db)
    return {
        "smtp_host": config.get("smtp_host", ""),
        "smtp_port": config.get("smtp_port", 587),
        "smtp_username": config.get("smtp_username", ""),
        "smtp_use_tls": config.get("smtp_use_tls", True),
        "smtp_password_set": bool(config.get("smtp_password", "")),
        "from_email": config.get("from_email", ""),
    }


@router.put("/smtp")
async def update_smtp_config(
    config_update: dict,
    current_user: Annotated[User, Depends(get_current_active_super_admin_user)],
    db: AsyncSession = Depends(get_db),
):
    """更新 SMTP 邮件外发服务器配置（super_admin 权限）"""
    from infrastructure.core.email import get_smtp_config as _read

    current = await _read(db)
    for key in ("smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_use_tls", "from_email"):
        if key in config_update:
            val = config_update[key]
            if key == "smtp_password" and not val:
                continue
            current[key] = val

    stmt = select(ProjectDashboardConfig).where(ProjectDashboardConfig.config_key == SMTP_CONFIG_KEY)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row:
        row.config_value = current
    else:
        db.add(ProjectDashboardConfig(config_key=SMTP_CONFIG_KEY, config_value=current, description="SMTP 邮件服务器配置"))

    await db.commit()
    logger.info(f"SMTP config updated by {current_user.username}")
    return {
        "success": True,
        "message": "SMTP 配置已更新",
        "smtp_host": current.get("smtp_host", ""),
        "smtp_port": current.get("smtp_port", 587),
        "smtp_username": current.get("smtp_username", ""),
        "smtp_use_tls": current.get("smtp_use_tls", True),
        "smtp_password_set": bool(current.get("smtp_password", "")),
        "from_email": current.get("from_email", ""),
    }


@router.post("/smtp/test")
async def test_smtp_connection(
    current_user: Annotated[User, Depends(get_current_active_admin_user)],
    db: AsyncSession = Depends(get_db),
):
    """测试 SMTP 连通性（admin 权限）"""
    from email.mime.text import MIMEText

    import aiosmtplib

    from infrastructure.core.email import get_smtp_config as _read

    config = await _read(db)
    host = config.get("smtp_host", "")
    port = int(config.get("smtp_port", 587))
    username = config.get("smtp_username", "")
    password = config.get("smtp_password", "")
    use_tls = bool(config.get("smtp_use_tls", True))

    if not host:
        raise HTTPException(status_code=400, detail="SMTP 主机未配置")

    use_implicit = use_tls and port == 465
    use_starttls = use_tls and port != 465

    steps = []
    try:
        steps.append(f"连接 {host}:{port}...")
        smtp = aiosmtplib.SMTP(hostname=host, port=port, use_tls=use_implicit, timeout=15)
        await smtp.connect()
        steps.append("✓ 连接成功")

        if use_starttls:
            steps.append("启动 STARTTLS...")
            await smtp.starttls(validate_certs=False)
            steps.append("✓ STARTTLS 成功")

        if username and password:
            steps.append(f"登录 {username}...")
            await smtp.login(username, password)
            steps.append("✓ 登录成功")
        elif username:
            steps.append("(跳过登录，未提供密码)")

        steps.append("发送测试邮件...")
        msg = MIMEText("vLLM Ascend Dashboard SMTP 连通性测试", "plain", "utf-8")
        msg["Subject"] = "SMTP 连通性测试"
        msg["From"] = config.get("from_email", username or "test@localhost")
        msg["To"] = config.get("from_email", username or "test@localhost")
        await smtp.send_message(msg)
        steps.append("✓ 测试邮件发送成功")

        await smtp.quit()
        return {"success": True, "message": "SMTP 连通性测试通过", "steps": steps}
    except Exception as e:
        steps.append(f"✗ 失败: {e}")
        try:
            await smtp.quit()
        except Exception:
            pass
        return {"success": False, "message": f"测试失败: {e}", "steps": steps}
