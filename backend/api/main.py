"""
vLLM Ascend Dashboard - Backend Application
"""
import logging
import os
import sys
from contextlib import asynccontextmanager

# 修复 Windows 控制台 GBK 编码问题（如 ⚠ 等 Unicode 字符无法输出）
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    for s in (sys.stdout, sys.stderr):
        try:
            if hasattr(s, 'reconfigure'):
                s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.usage_tracking import (
    UsageTrackingMiddleware,
    start_usage_tracking_worker,
    stop_usage_tracking_worker,
)
from api.v1 import (
    alert_rules,
    auth,
    ci,
    code_metrics,
    commit_analysis,
    contributor_board,
    contributor_scoring,
    daily_report,
    daily_summary,
    issue_diagnosis,
    job_owners,
    logs,
    model_sync_configs,
    models,
    npu_occupancy,
    npu_queue,
    performance,
    pr_pipeline,
    project_dashboard,
    release_management,
    resource_dashboard,
    resource_metrics,
    stats,
    system_config,
    test_board,
    users,
    workflows,
)
from infrastructure.core.config import settings
from infrastructure.core.logging import setup_db_logging
from infrastructure.db.base import engine
from infrastructure.persistence.models import Base

# Database DDL is allowed only for local development. Production migrations
# must be executed by the explicit release/migration job before the API starts.
_AUTO_MIGRATE = os.environ.get(
    "AUTO_MIGRATE",
    "false" if os.environ.get("ENVIRONMENT", "development").lower() == "production" else "true",
).lower() == "true"

# 配置日志
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)

# 降低第三方库日志级别，避免打印无用信息
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
# 注意：apscheduler.scheduler 保持默认级别，以便记录调度器执行日志
# 如果 LOG_LEVEL=WARNING，调度器的 INFO 日志会被过滤，这是正常的

logger = logging.getLogger(__name__)


async def init_db():
    """初始化数据库表"""
    if not _AUTO_MIGRATE:
        logger.info("Automatic database initialization is disabled; use the explicit migration job")
        return

    # 尝试建表；若库中已有完整 schema（如 Phase B 视图），则跳过
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.warning("Table creation skipped (DB may already have schema): %s", e)

    # 初始化 LLM 提供商默认配置
    await _init_llm_provider_configs()

    # 同步 provider 配置到 LiteLLM 网关（生产环境）
    await _sync_litellm_providers()

    # Claude Code CLI 预热检查 —— 后台执行，不阻塞启动

    # 清理启动前遗留的僵尸分析记录（>1h 还在 analyzing 的标记为 failed）
    await _cleanup_stale_analyses()


async def legacy_schema_upgrade_test_case_columns():
    """Ensure test_cases table has lifetime counters and admin-maintained columns.

    create_all won't ALTER existing tables, so add missing columns for existing DBs.
    """
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            def _get_columns(sync_session):
                rows = sync_session.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'test_cases'"
                )).fetchall()
                return [r[0] for r in rows]
            existing_cols = await db.run_sync(_get_columns)

            # (column_name, mysql_type)
            new_columns = [
                ("lifetime_runs", "INT DEFAULT 0"),
                ("lifetime_failures", "INT DEFAULT 0"),
                ("issues_found", "INT DEFAULT 0"),
                ("suspected_test_issue_count", "INT DEFAULT 0"),
                ("is_flaky_manual", "BOOLEAN DEFAULT FALSE"),
                ("auto_issues_found", "INT DEFAULT 0"),
                ("auto_suspected_test_issue_count", "INT DEFAULT 0"),
                ("issues_found_override", "BOOLEAN DEFAULT FALSE"),
            ]

            added = []
            for col_name, col_type in new_columns:
                if col_name not in existing_cols:
                    logger.info("Adding missing column '%s' to test_cases", col_name)
                    await db.execute(text(f"ALTER" f" TABLE test_cases ADD COLUMN {col_name} {col_type}"))
                    added.append(col_name)

            if added:
                await db.commit()
                logger.info("Added test_cases columns: %s", ", ".join(added))

            # 回填生命周期计数：仅能从 test_runs 保留期（默认 90 天）内的数据回填，
            # 不包含更早的历史，因此迁移前老用例的值为近似值；之后的新增采集中会持续累加。
            if "lifetime_runs" in added:
                await db.execute(text(
                    "UPDATE test_cases SET lifetime_runs = ("
                    "  SELECT COUNT(*) FROM test_runs WHERE test_runs.test_case_id = test_cases.id"
                    ") WHERE lifetime_runs IS NULL OR lifetime_runs = 0"
                ))
                await db.execute(text(
                    "UPDATE test_cases SET lifetime_failures = ("
                    "  SELECT COUNT(*) FROM test_runs WHERE test_runs.test_case_id = test_cases.id"
                    "    AND test_runs.result = 'failed'"
                    ") WHERE lifetime_failures IS NULL OR lifetime_failures = 0"
                ))
                await db.commit()
                logger.info("Backfilled lifetime_runs / lifetime_failures from test_runs")

            # 为 is_flaky_manual 创建索引（MySQL < 8.0.29 不支持 IF NOT EXISTS，先查再建）
            if "is_flaky_manual" in added:
                try:
                    idx_exists = (await db.execute(text(
                        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'test_cases' "
                        "AND INDEX_NAME = 'ix_test_cases_is_flaky_manual'"
                    ))).scalar()
                    if not idx_exists:
                        await db.execute(text(
                            "CREATE INDEX ix_test_cases_is_flaky_manual ON test_cases (is_flaky_manual)"
                        ))
                    await db.commit()
                except Exception as idx_err:
                    logger.warning("is_flaky_manual index creation skipped (non-fatal): %s", idx_err)

    except Exception as e:
        logger.warning(f"test_cases column migration skipped (non-fatal): {e}")


async def legacy_schema_upgrade_third_party_columns():
    """Ensure release_third_party_versions table has repository_url column."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            def _get_columns(sync_session):
                rows = sync_session.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'release_third_party_versions'"
                )).fetchall()
                return [r[0] for r in rows]
            existing_cols = await db.run_sync(_get_columns)

            if "repository_url" not in existing_cols:
                logger.info("Adding missing column 'repository_url' to release_third_party_versions")
                await db.execute(text(
                    "ALTER TABLE release_third_party_versions ADD COLUMN repository_url VARCHAR(500) DEFAULT NULL"
                ))
                await db.commit()
    except Exception as e:
        logger.warning(f"release_third_party_versions column migration skipped (non-fatal): {e}")


async def legacy_schema_upgrade_contributor_metrics_columns():
    """Ensure contributor_metrics table has PR-efficiency columns for recalc-from-prs."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            def _get_columns(sync_session):
                rows = sync_session.execute(text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contributor_metrics'"
                )).fetchall()
                return [r[0] for r in rows]
            existing_cols = await db.run_sync(_get_columns)

            new_columns = [
                ("ci_failure_count", "INT DEFAULT 0"),
                ("first_review_hours_avg", "FLOAT DEFAULT NULL"),
                ("merge_hours_avg", "FLOAT DEFAULT NULL"),
                ("ci_duration_hours_avg", "FLOAT DEFAULT NULL"),
            ]
            added = []
            for col_name, col_type in new_columns:
                if col_name not in existing_cols:
                    logger.info("Adding missing column '%s' to contributor_metrics", col_name)
                    await db.execute(text(f"ALTER TABLE contributor_metrics ADD COLUMN {col_name} {col_type}"))
                    added.append(col_name)
            if added:
                await db.commit()
                logger.info("Added contributor_metrics columns: %s", ", ".join(added))
    except Exception as e:
        logger.warning(f"contributor_metrics column migration skipped (non-fatal): {e}")


async def _warmup_claude_code_cli():
    """Claude Code CLI 预热：验证 CLI 可用 + API key 有效"""
    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from infrastructure.clients.claude_code_cli import ClaudeCodeCLI
        from infrastructure.persistence.models.daily_summary import LLMProviderConfig

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            stmt = select(LLMProviderConfig).where(LLMProviderConfig.is_active).limit(1)
            result = await db.execute(stmt)
            config = result.scalar_one_or_none()

        if not config or not config.api_key:
            logger.warning("No active LLM provider configured, skipping CLI warmup")
            return

        cli = ClaudeCodeCLI()
        ok = await cli.ensure_initialized({
            "provider": config.provider,
            "api_key": config.api_key,
            "api_base_url": config.api_base_url or "",
            "default_model": config.default_model,
        })

        if ok:
            logger.info("Claude Code CLI warmup successful")
        else:
            logger.warning("Claude Code CLI warmup failed — analysis will fallback to direct API")
    except Exception as e:
        logger.warning("Claude Code CLI warmup error (non-fatal): %s", e)


async def _cleanup_stale_analyses():
    """启动时清理残留的 analyzing 记录（区分超时与重启）"""
    try:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            from sqlalchemy import and_, or_, update

            from infrastructure.persistence.models import JobFailureAnalysis

            # Use the last progress update rather than created_at.  A queued
            # record may be old while its Collector task is actively running;
            # using created_at would mark a healthy long-running analysis as
            # failed during an unrelated API restart.
            cutoff = datetime.now(UTC) - timedelta(hours=2)
            stale_marker = or_(
                JobFailureAnalysis.updated_at < cutoff,
                and_(
                    JobFailureAnalysis.updated_at.is_(None),
                    JobFailureAnalysis.created_at < cutoff,
                ),
            )

            # Only stale records are cleaned.  Recent rows are left alone so a
            # backend restart cannot interrupt an analysis owned by Collector.
            result = await db.execute(
                update(JobFailureAnalysis)
                .where(
                    JobFailureAnalysis.analysis_status == "analyzing",
                    stale_marker,
                    or_(
                        JobFailureAnalysis.analysis_phase.is_(None),
                        JobFailureAnalysis.analysis_phase != "completed",
                    ),
                )
                .values(
                    analysis_status="failed",
                    analysis_phase="failed",
                    error_message="分析超时（超过 2 小时无进度），启动时自动标记为失败",
                )
            )
            if result.rowcount:
                logger.warning("Timed out %d stale analysis records", result.rowcount)

            await db.commit()
    except Exception as e:
        logger.warning("Failed to cleanup stale analyses (non-fatal): %s", e)


async def _migrate_nightly_test_cases_columns():
    """Add report_date + source_branch to nightly_test_cases, update unique constraint"""
    try:
        from sqlalchemy import inspect, text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            def _get_columns(sync_session):
                return {c["name"] for c in inspect(sync_session.connection()).get_columns("nightly_test_cases")}
            existing = await db.run_sync(_get_columns)
            for name, sql_type in [("report_date", "DATE"), ("source_branch", "VARCHAR(100) DEFAULT 'main'")]:
                if name not in existing:
                    await db.execute(text(f"ALTER" f" TABLE nightly_test_cases ADD COLUMN {name} {sql_type}"))
                    await db.commit()
            # Drop old unique constraint, add new one
            try:
                await db.execute(text("ALTER" " TABLE nightly_test_cases DROP INDEX uq_nightly_test_case_workflow_job"))
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute(text(
                    "ALTER" " TABLE nightly_test_cases ADD UNIQUE INDEX uq_nightly_test_case_date_branch_wf_job"
                    " (report_date, source_branch, workflow_name, job_name)"
                ))
                await db.commit()
            except Exception:
                pass
    except Exception as e:
        logger.warning("Nightly test case columns migration skipped (non-fatal): %s", e)


async def _migrate_daily_failure_records_columns():
    """Add source_branch + problem_category to daily_failure_records, update unique constraint"""
    try:
        from sqlalchemy import inspect, text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            def _get_columns(sync_session):
                return {c["name"] for c in inspect(sync_session.connection()).get_columns("daily_failure_records")}
            existing = await db.run_sync(_get_columns)
            if "source_branch" not in existing:
                await db.execute(text("ALTER" " TABLE daily_failure_records ADD COLUMN source_branch VARCHAR(100) DEFAULT 'main'"))
                await db.commit()
            if "problem_category" not in existing:
                await db.execute(text("ALTER" " TABLE daily_failure_records ADD COLUMN problem_category VARCHAR(50)"))
                await db.commit()
            if "related_pr" not in existing:
                await db.execute(text("ALTER" " TABLE daily_failure_records ADD COLUMN related_pr VARCHAR(20)"))
                await db.commit()
            if "processing_time" not in existing:
                await db.execute(text("ALTER" " TABLE daily_failure_records ADD COLUMN processing_time TIMESTAMP NULL"))
                await db.commit()
            if "closure_time" not in existing:
                await db.execute(text("ALTER" " TABLE daily_failure_records ADD COLUMN closure_time TIMESTAMP NULL"))
                await db.commit()
            try:
                await db.execute(text("ALTER" " TABLE daily_failure_records DROP INDEX uq_daily_failure_date_wf_job"))
                await db.commit()
            except Exception:
                pass
            try:
                await db.execute(text(
                    "ALTER" " TABLE daily_failure_records ADD UNIQUE INDEX uq_daily_failure_date_branch_wf_job"
                    " (report_date, source_branch, workflow_name, job_name)"
                ))
                await db.commit()
            except Exception:
                pass
    except Exception as e:
        logger.warning("Daily failure records columns migration skipped (non-fatal): %s", e)


async def _sync_litellm_providers():
    """同步启用的 LLM provider 到 LiteLLM 网关"""
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from model_sync.litellm_sync import get_litellm_sync

        sync = get_litellm_sync()
        if not sync.available:
            logger.info("LiteLLM not configured (LITELLM_PROXY_URL not set), skipping sync")
            return

        # 直接写配置文件，不依赖 health check
        # LiteLLM 可能还没就绪，但文件写入后下次启动就会读取
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            count = await sync.sync_from_db(db)
            logger.info(f"Synced {count} providers to LiteLLM")
    except Exception as e:
        logger.warning(f"LiteLLM provider sync failed (non-fatal): {e}")


async def _init_llm_provider_configs():
    """初始化 LLM 提供商默认配置"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from infrastructure.persistence.models.daily_summary import LLMProviderConfig

    # 创建临时会话
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        try:
            # 检查是否已有配置
            stmt = select(LLMProviderConfig)
            result = await db.execute(stmt)
            existing = result.scalars().first()

            if existing:
                logger.info("LLM provider configs already exist, skipping initialization")
                return

            # 添加默认配置
            default_configs = [
                LLMProviderConfig(
                    provider='openai',
                    display_name='OpenAI API',
                    api_base_url='https://api.openai.com/v1',
                    default_model='gpt-4o',
                    enabled=True,
                    is_active=False,
                    display_order=1,
                ),
                LLMProviderConfig(
                    provider='anthropic',
                    display_name='Anthropic Claude',
                    api_base_url='https://api.anthropic.com',
                    default_model='claude-sonnet-4-20250514',
                    enabled=True,
                    is_active=False,
                    display_order=2,
                ),
                LLMProviderConfig(
                    provider='qwen',
                    display_name='通义千问',
                    api_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
                    default_model='qwen-plus',
                    enabled=True,
                    is_active=False,
                    display_order=3,
                ),
            ]

            for config in default_configs:
                db.add(config)

            await db.commit()
            logger.info("LLM provider configs initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize LLM provider configs: {e}", exc_info=True)
            await db.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting vLLM Ascend Dashboard application...")

    # 启动时初始化数据库（含 app_logs 表）
    await init_db()

    await legacy_schema_upgrade_third_party_columns()
    await legacy_schema_upgrade_contributor_metrics_columns()

    try:
        from infrastructure.core.app_runtime_config import load_app_runtime_config
        from infrastructure.core.github_config import load_github_runtime_config

        await load_app_runtime_config()
        await load_github_runtime_config()
    except Exception as exc:
        logger.warning("Failed to load shared runtime configuration: %s", exc)

    # 安装 DB 日志处理器，将应用日志持久化到数据库
    # 放在 init_db 之后：确保 app_logs 表已创建，worker 首次 flush 即可成功
    # 事件循环安全：worker 的阻塞 queue.get 已通过 run_in_executor 移出事件循环
    try:
        setup_db_logging()
    except Exception as e:
        logger.warning("setup_db_logging failed (non-fatal): %s", e)

    # Usage analytics are written by one bounded background worker instead of
    # opening a second DB session for every HTTP response.
    await start_usage_tracking_worker()

    yield

    logger.info("Shutting down application...")

    try:
        await stop_usage_tracking_worker()
        await engine.dispose()
        logger.info("Database engine disposed successfully")
    except Exception as e:
        logger.error(f"Error disposing database engine: {e}", exc_info=True)


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""

    app = FastAPI(
        title="vLLM Ascend Dashboard API",
        description="vLLM Ascend 社区看板后端 API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # 配置 CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:5000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(UsageTrackingMiddleware)

    # 注册路由
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["认证"])
    app.include_router(ci.router, prefix="/api/v1/ci", tags=["CI 数据"])
    app.include_router(commit_analysis.router, prefix="/api/v1/commit-analysis", tags=["Commit 分析"])
    app.include_router(daily_summary.router, prefix="/api/v1", tags=["每日总结"])
    app.include_router(models.router, prefix="/api/v1/models", tags=["模型管理"])
    app.include_router(model_sync_configs.router, prefix="/api/v1/model-sync-configs", tags=["模型同步配置"])
    app.include_router(performance.router, prefix="/api/v1/performance", tags=["性能数据"])
    app.include_router(users.router, prefix="/api/v1/users", tags=["用户管理"])
    app.include_router(workflows.router, prefix="/api/v1/workflows", tags=["Workflow 配置"])
    app.include_router(job_owners.router, prefix="/api/v1/job-owners", tags=["Job 责任人"])
    app.include_router(system_config.router, prefix="/api/v1/system/config", tags=["系统配置"])
    app.include_router(project_dashboard.router, prefix="/api/v1/project-dashboard", tags=["项目看板"])
    app.include_router(resource_dashboard.router, prefix="/api/v1/resource-dashboard", tags=["资源看板"])
    app.include_router(resource_metrics.router, prefix="/api/v1/resource-dashboard", tags=["资源看板"])
    app.include_router(npu_occupancy.router, prefix="/api/v1/npu-occupancy", tags=["NPU 卡占用"])
    app.include_router(npu_queue.router, prefix="/api/v1/npu-queue", tags=["NPU 排队与需求"])
    app.include_router(daily_report.router, prefix="/api/v1", tags=["每日运行报告"])
    app.include_router(stats.router, prefix="/api/v1/stats", tags=["统计信息"])
    app.include_router(issue_diagnosis.router, prefix="/api/v1/issue-diagnosis", tags=["问题定位"])
    app.include_router(alert_rules.router, prefix="/api/v1", tags=["告警规则"])
    app.include_router(pr_pipeline.router, prefix="/api/v1/pr-pipeline", tags=["PR 流水线"])
    app.include_router(test_board.router, prefix="/api/v1", tags=["测试看板"])
    app.include_router(logs.router, prefix="/api/v1/logs", tags=["日志中心"])
    app.include_router(code_metrics.router, prefix="/api/v1/code-metrics", tags=["代码度量"])
    app.include_router(contributor_board.router, prefix="/api/v1/contributor-board", tags=["贡献者看板"])
    app.include_router(contributor_scoring.router, prefix="/api/v1/contributor-board/scoring", tags=["贡献者评分/权重版本"])
    app.include_router(release_management.router, prefix="/api/v1", tags=["发布件管理"])

    @app.get("/health")
    async def health_check():
        """健康检查接口"""
        return {
            "status": "healthy",
            "version": "0.1.0",
            "environment": settings.ENVIRONMENT,
        }

    # ── 全局异常处理器 ──────────────────────────────────────────

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        """
        全局异常处理器，返回统一 JSON 格式：
        {"code": int, "msg": str, "data": null}
        """
        from fastapi import HTTPException as FastAPIHTTPException
        from fastapi.responses import JSONResponse
        import traceback

        from infrastructure.core.exceptions import BusinessException, StatusCode

        def _respond(status: StatusCode, msg: str) -> JSONResponse:
            return JSONResponse(
                status_code=int(status),
                content={"code": int(status), "msg": msg, "data": None},
            )

        # HTTPException → 透传原始状态码
        if isinstance(exc, FastAPIHTTPException):
            status = StatusCode.from_http_status(exc.status_code)
            return _respond(status, str(exc.detail))

        # 业务异常 → 按枚举映射
        if isinstance(exc, BusinessException):
            msg = exc.msg
            if settings.DEBUG and exc.detail is not None:
                msg = f"{msg} [{exc.detail}]"
            return _respond(exc.status_code, msg)

        # 编程错误 → 500
        if isinstance(exc, (NameError, TypeError, AttributeError, KeyError)):
            detail = f"{type(exc).__name__}: {exc}" if settings.DEBUG else StatusCode.INTERNAL.default_msg
            return _respond(StatusCode.INTERNAL, detail)

        # 超时/连接 → 503
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return _respond(StatusCode.SERVICE_UNAVAILABLE, StatusCode.SERVICE_UNAVAILABLE.default_msg)

        # 其他未知 → 500
        detail = f"{type(exc).__name__}: {exc}" if settings.DEBUG else StatusCode.INTERNAL.default_msg
        logger.error(
            "Unhandled exception on %s %s: %s\n%s",
            request.method, request.url.path, exc, traceback.format_exc(),
        )
        return _respond(StatusCode.INTERNAL, detail)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
