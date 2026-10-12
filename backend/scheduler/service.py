"""
数据同步定时任务调度器
使用 APScheduler 实现定时数据采集
"""
import logging
from datetime import UTC, date, datetime, timedelta

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import bindparam, text

from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.db.base import SessionLocal

logger = logging.getLogger(__name__)


async def _read_config_async(config_key: str) -> dict | None:
    """从数据库读取配置（仅限异步上下文调用）。"""
    try:
        from sqlalchemy import select as sa_select

        from infrastructure.persistence.models import ProjectDashboardConfig
        async with SessionLocal() as db:
            stmt = sa_select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == config_key
            )
            result = await db.execute(stmt)
            row = result.scalar_one_or_none()
            if row and row.config_value:
                return dict(row.config_value)
            return None
    except Exception as e:
        logger.warning(f"Failed to read config '{config_key}' from database: {e}")
        return None


class DataSyncScheduler:
    """
    数据同步定时任务调度器

    功能：
    - 定时同步 CI 数据（可配置间隔）
    - 手动触发同步
    - 任务执行监控
    """

    def __init__(self):
        """初始化调度器（不读 DB，DB 配置由 apply_db_config_overrides 异步加载）"""
        self._timezone = 'Asia/Shanghai'

        self.scheduler = AsyncIOScheduler(
            timezone=self._timezone,
            job_defaults={
                'coalesce': True,  # 合并错过的执行
                'max_instances': 1,  # 同一任务最多只有 1 个实例运行
                'misfire_grace_time': 60,  # 错过执行的容忍时间（秒）
            }
        )

        # 任务执行监听
        self.scheduler.add_listener(
            self._job_event_listener,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )

        self.github_client: GitHubClient | None = None
        self._initialized = False

        logger.info("DataSyncScheduler initialized")

    def _job_event_listener(self, event):
        """任务执行事件监听"""
        if event.exception:
            logger.error(f"Job {event.job_id} failed: {event.exception}")
        else:
            logger.info(f"Job {event.job_id} executed successfully at {datetime.now()}")

    def start(self) -> None:
        """启动调度器"""
        logger.info("=" * 60)
        logger.info("SCHEDULER STARTING - Adding scheduled jobs")
        logger.info("=" * 60)

        if not self._initialized:
            self._initialize_github_client()

        # CI 数据同步任务 - 可配置间隔（默认 30 分钟，与生产保持一致）
        sync_interval_minutes = getattr(settings, 'CI_SYNC_INTERVAL_MINUTES', 30)

        try:
            self.scheduler.add_job(
                self._sync_ci_data_job,
                trigger=IntervalTrigger(minutes=sync_interval_minutes),
                id="ci_data_sync",
                name="CI Data Sync",
                replace_existing=True,
            )
            logger.info(f"[1/4] CI data sync scheduled every {sync_interval_minutes} minutes")
        except Exception as e:
            logger.error(f"Failed to add CI data sync job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                self._sync_npu_occupancy_job,
                trigger=IntervalTrigger(minutes=settings.NPU_OCCUPANCY_SYNC_INTERVAL_MINUTES),
                id="npu_occupancy_sync",
                name="NPU Occupancy Raw Env Sync",
                replace_existing=True,
            )
            logger.info("NPU occupancy sync scheduled every %s minutes", settings.NPU_OCCUPANCY_SYNC_INTERVAL_MINUTES)
        except Exception as e:
            logger.error("Failed to add NPU occupancy sync job: %s", e, exc_info=True)

        # Repository snapshots are refreshed centrally once per day. Analysis
        # tasks consume this persistent snapshot and never pull on demand.
        settings.PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES = 1440
        try:
            self.scheduler.add_job(
                self._update_project_dashboard_cache_job,
                trigger=CronTrigger(hour=4, minute=30, timezone=self._timezone),
                id="project_dashboard_cache_update",
                name="Repository Mirror Refresh",
                replace_existing=True,
            )
            logger.info("[2/4] Repository mirror refresh scheduled daily at 04:30")
        except Exception as e:
            logger.error(f"Failed to add project dashboard cache update job: {e}", exc_info=True)

        # 模型报告同步任务 - 可配置间隔（默认 60 分钟）
        model_sync_interval = getattr(settings, 'MODEL_SYNC_INTERVAL_MINUTES', 60)
        try:
            self.scheduler.add_job(
                self._sync_model_reports_job,
                trigger=IntervalTrigger(minutes=model_sync_interval),
                id="model_report_sync",
                name="Model Report Sync",
                replace_existing=True,
            )
            logger.info(f"[3/4] Model report sync scheduled every {model_sync_interval} minutes")
        except Exception as e:
            logger.error(f"Failed to add model report sync job: {e}", exc_info=True)

        # 每日总结生成任务 - 每天早上 8 点执行（可配置）
        try:
            cron_hour = getattr(settings, 'DAILY_SUMMARY_CRON_HOUR', 8)
            cron_minute = getattr(settings, 'DAILY_SUMMARY_CRON_MINUTE', 0)
            enabled = getattr(settings, 'DAILY_SUMMARY_ENABLED', True)

            if enabled:
                self.scheduler.add_job(
                    self._generate_daily_summary_job,
                    trigger=CronTrigger(hour=cron_hour, minute=cron_minute, timezone=self._timezone),
                    id="daily_summary_task",
                    name="Generate Daily Summary",
                    replace_existing=True,
                )
                logger.info(f"[4/4] Daily summary generation scheduled at {cron_hour}:{cron_minute:02d} {self._timezone} (enabled={enabled})")
            else:
                logger.info(f"[4/4] Daily summary generation DISABLED (enabled={enabled})")
        except Exception as e:
            logger.error(f"Failed to add daily summary job: {e}", exc_info=True)

        # 启动调度器
        if not self.scheduler.running:
            try:
                self.scheduler.start()
                logger.info("=" * 60)
                logger.info("SCHEDULER STARTED SUCCESSFULLY")
                jobs = self.scheduler.get_jobs()
                logger.info(f"Total jobs scheduled: {len(jobs)}")
                for job in jobs:
                    logger.info(f"  - {job.id}: {job.name}, next_run={job.next_run_time}")
                logger.info("=" * 60)
            except Exception as e:
                logger.error(f"Failed to start scheduler: {e}", exc_info=True)
        else:
            logger.info("Scheduler already running")

        # 心跳任务 — 让独立 scheduler 进程的存活状态对 API 进程可见
        # （API 进程内的 APScheduler 为空，无法直接感知独立 scheduler）
        try:
            self.scheduler.add_job(
                self.write_heartbeat,
                trigger=IntervalTrigger(seconds=20),
                id="scheduler_heartbeat",
                name="Scheduler Heartbeat",
                replace_existing=True,
            )
            logger.info("Scheduler heartbeat scheduled every 20s")
        except Exception as e:
            logger.error(f"Failed to add heartbeat job: {e}", exc_info=True)

        # Control-plane events are durable MySQL commands emitted by the API.
        # They replace cross-process mutation of this APScheduler instance.
        try:
            self.scheduler.add_job(
                self._consume_control_events,
                trigger=IntervalTrigger(seconds=5),
                id="scheduler_control_plane",
                name="Scheduler Control Plane",
                replace_existing=True,
            )
        except Exception as e:
            logger.error("Failed to add control-plane consumer: %s", e, exc_info=True)

        # NPU 指标采集任务 - 默认每 1 分钟执行
        try:
            metrics_interval = getattr(settings, 'RESOURCE_METRICS_INTERVAL_MINUTES', 1)

            self.scheduler.add_job(
                self._collect_resource_metrics_job,
                trigger=IntervalTrigger(minutes=metrics_interval),
                id="resource_metrics_collect",
                name="Resource Metrics Collect",
                replace_existing=True,
            )
            logger.info(f"Resource metrics collection scheduled every {metrics_interval} minutes")
        except Exception as e:
            logger.error(f"Failed to add resource metrics collection job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                self._collect_npu_queue_snapshot_job,
                trigger=IntervalTrigger(minutes=settings.NPU_QUEUE_SNAPSHOT_INTERVAL_MINUTES),
                id="npu_queue_snapshot",
                name="NPU Queue Snapshot",
                replace_existing=True,
            )
            logger.info("NPU queue snapshots scheduled every %d minutes", settings.NPU_QUEUE_SNAPSHOT_INTERVAL_MINUTES)
        except Exception as e:
            logger.error("Failed to add NPU queue snapshot job: %s", e, exc_info=True)

        # NPU 指标数据清理任务 - 每天凌晨 00:00 执行
        try:
            self.scheduler.add_job(
                self._cleanup_resource_metrics_job,
                trigger=CronTrigger(hour=0, minute=0, timezone=self._timezone),
                id="resource_metrics_cleanup",
                name="Resource Metrics Cleanup",
                replace_existing=True,
            )
            logger.info(f"Resource metrics cleanup scheduled at 00:00 {self._timezone}")
        except Exception as e:
            logger.error(f"Failed to add resource metrics cleanup job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                self._cleanup_npu_queue_snapshots_job,
                trigger=CronTrigger(hour=0, minute=10, timezone=self._timezone),
                id="npu_queue_snapshot_cleanup",
                name="NPU Queue Snapshot Cleanup",
                replace_existing=True,
            )
        except Exception as e:
            logger.error("Failed to add NPU queue snapshot cleanup job: %s", e, exc_info=True)

        # 失败分析不在 Scheduler 内执行；Collector 在 CI/Nightly 数据物化后
        # 将新失败任务作为 durable failure_analysis 任务入队。

        # 每日运行报告邮件推送任务 - 默认 8:30 执行（DB 中的时间由 apply_db_config_overrides 覆盖）
        try:
            report_enabled = getattr(settings, 'REPORT_ENABLED', True)
            report_hour = getattr(settings, 'REPORT_SCHEDULE_HOUR', 8)
            report_minute = getattr(settings, 'REPORT_SCHEDULE_MINUTE', 30)
            local_tz = self._timezone

            if report_enabled:
                self.scheduler.add_job(
                    self._send_daily_report_job,
                    trigger=CronTrigger(hour=report_hour, minute=report_minute, timezone=local_tz),
                    id="daily_report_task",
                    name="Daily Report Email",
                    replace_existing=True,
                )
                logger.info(f"Daily report email scheduled at {report_hour}:{report_minute:02d} {local_tz} (enabled={report_enabled})")
            else:
                logger.info(f"Daily report email DISABLED (enabled={report_enabled})")
        except Exception as e:
            logger.error(f"Failed to add daily report job: {e}", exc_info=True)

        # CI 失败分析由 Collector 在 CI 数据物化后异步入队，避免阻塞 Scheduler。

        pr_pipeline_interval = getattr(settings, 'PR_PIPELINE_SYNC_INTERVAL_MINUTES', 30)
        try:
            self.scheduler.add_job(
                self._sync_pr_pipeline_job,
                trigger=IntervalTrigger(minutes=pr_pipeline_interval),
                id="pr_pipeline_sync",
                name="PR Pipeline Sync",
                replace_existing=True,
            )
            logger.info(f"PR pipeline sync scheduled every {pr_pipeline_interval} minutes")
        except Exception as e:
            logger.error(f"Failed to add PR pipeline sync job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                self._cleanup_logs_job,
                trigger=IntervalTrigger(hours=6),
                id="cleanup_logs",
                name="Cleanup Expired Logs and Tokens",
                replace_existing=True,
            )
            logger.info("Log cleanup scheduled every 6 hours")
        except Exception as e:
            logger.error(f"Failed to add log cleanup job: {e}", exc_info=True)

        test_board_interval = getattr(settings, 'TEST_BOARD_SYNC_INTERVAL_MINUTES', 120)
        nightly_data_interval = getattr(settings, 'NIGHTLY_DATA_SYNC_INTERVAL_MINUTES', test_board_interval)
        try:
            self.scheduler.add_job(
                self._sync_nightly_data_job,
                trigger=IntervalTrigger(minutes=nightly_data_interval),
                id="nightly_data_sync",
                name="Nightly Config and Failure Materialization",
                next_run_time=datetime.now(UTC),
                replace_existing=True,
            )
            logger.info(
                "Nightly config snapshot and daily failure materialization scheduled "
                "every %s minutes",
                nightly_data_interval,
            )
        except Exception as e:
            logger.error("Failed to add Nightly data sync job: %s", e, exc_info=True)

        try:
            self.scheduler.add_job(
                self._parse_test_results_job,
                trigger=IntervalTrigger(minutes=test_board_interval),
                id="test_result_parse",
                name="Test Board Result Parse",
                replace_existing=True,
            )
            logger.info(f"Test board result parse scheduled every {test_board_interval} minutes")
        except Exception as e:
            logger.error(f"Failed to add test board result parse job: {e}", exc_info=True)

        coverage_interval = max(int(getattr(settings, "COVERAGE_SYNC_INTERVAL_MINUTES", 60)), 10)
        try:
            self.scheduler.add_job(
                self._sync_coverage_job,
                trigger=IntervalTrigger(minutes=coverage_interval),
                id="coverage_sync",
                name="Test Coverage Sync",
                replace_existing=True,
            )
            logger.info("Test coverage sync scheduled every %s minutes", coverage_interval)
        except Exception as e:
            logger.error("Failed to add coverage sync job: %s", e, exc_info=True)

        try:
            self.scheduler.add_job(
                self._sync_coverage_daily_baseline_job,
                trigger=CronTrigger(
                    hour=int(getattr(settings, "COVERAGE_DAILY_SYNC_HOUR", 3)),
                    minute=int(getattr(settings, "COVERAGE_DAILY_SYNC_MINUTE", 15)),
                    timezone=self._timezone,
                ),
                id="coverage_daily_baseline",
                name="Test Coverage Daily Baseline",
                replace_existing=True,
            )
        except Exception as e:
            logger.error("Failed to add daily coverage baseline job: %s", e, exc_info=True)

        try:
            self.scheduler.add_job(
                self._calc_test_health_job,
                trigger=IntervalTrigger(minutes=test_board_interval + 30),
                id="test_health_calc",
                name="Test Board Health Calc",
                replace_existing=True,
            )
            logger.info(f"Test board health calc scheduled every {test_board_interval + 30} minutes")
        except Exception as e:
            logger.error(f"Failed to add test health calc job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                self._snapshot_test_suites_job,
                trigger=IntervalTrigger(hours=6),
                id="test_suite_snapshot",
                name="Test Board Suite Snapshot",
                replace_existing=True,
            )
            logger.info("Test board suite snapshot scheduled every 6 hours")
        except Exception as e:
            logger.error(f"Failed to add test suite snapshot job: {e}", exc_info=True)

        try:
            self.scheduler.add_job(
                self._cleanup_test_runs_job,
                trigger=CronTrigger(hour=2, minute=0, timezone=self._timezone),
                id="cleanup_test_runs",
                name="Test Board Run Cleanup",
                replace_existing=True,
            )
            logger.info(f"Test board run cleanup scheduled at 02:00 {self._timezone}")
        except Exception as e:
            logger.error(f"Failed to add test board cleanup job: {e}", exc_info=True)

        # 已退出测试用例物理清理 — 每日凌晨 02:30 删除超过 STALE_CASE_DELETE_DAYS 天未运行的用例
        try:
            self.scheduler.add_job(
                self._cleanup_stale_cases_job,
                trigger=CronTrigger(hour=2, minute=30, timezone=self._timezone),
                id="cleanup_stale_cases",
                name="Test Board Stale Case Cleanup",
                replace_existing=True,
            )
            logger.info(f"Test board stale case cleanup scheduled at 02:30 {self._timezone}")
        except Exception as e:
            logger.error(f"Failed to add stale case cleanup job: {e}", exc_info=True)

        # 上游支持矩阵同步任务 - 每日同步
        if getattr(settings, 'SUPPORT_MATRIX_SYNC_ENABLED', True):
            try:
                sync_hour = getattr(settings, 'SUPPORT_MATRIX_SYNC_CRON_HOUR', 6)
                sync_minute = getattr(settings, 'SUPPORT_MATRIX_SYNC_CRON_MINUTE', 0)
                self.scheduler.add_job(
                    self._sync_support_matrix_job,
                    trigger=CronTrigger(hour=sync_hour, minute=sync_minute, timezone=self._timezone),
                    id="support_matrix_sync",
                    name="Sync Upstream Support Matrix",
                    replace_existing=True,
                )
                logger.info(f"Support matrix sync scheduled at {sync_hour}:{sync_minute:02d} {self._timezone}")
            except Exception as e:
                logger.error(f"Failed to add support matrix sync job: {e}", exc_info=True)

        # 代码度量定时清理
        try:
            self.scheduler.add_job(
                self._cleanup_code_metrics_job,
                trigger=CronTrigger(hour=3, minute=0, timezone=self._timezone),
                id="code_metrics_cleanup",
                name="Code Metrics Cleanup",
                replace_existing=True,
            )
            logger.info("Code metrics cleanup scheduled at 03:00 daily")
        except Exception as e:
            logger.error(f"Failed to add code metrics cleanup job: {e}")

        # 代码度量热力图同步
        try:
            self.scheduler.add_job(
                self._sync_heatmap_job,
                trigger=CronTrigger(hour=4, minute=0, timezone=self._timezone),
                id="code_metrics_heatmap_sync",
                name="Code Metrics Heatmap Sync",
                replace_existing=True,
            )
            logger.info("Code metrics heatmap sync scheduled at 04:00 daily")
        except Exception as e:
            logger.error(f"Failed to add heatmap sync job: {e}")

        # 代码度量本地采集
        try:
            self.scheduler.add_job(
                self._collect_code_metrics_job,
                trigger=CronTrigger(hour=5, minute=0, timezone=self._timezone),
                id="code_metrics_collect",
                name="Code Metrics Collection",
                replace_existing=True,
            )
            logger.info("Code metrics collection scheduled at 05:00 daily")
        except Exception as e:
            logger.error(f"Failed to add code metrics collection job: {e}")

    async def apply_db_config_overrides(self) -> None:
        """从数据库读取调度配置，覆盖默认值（异步，仅在事件循环内调用）。

        应在 start() 之后、事件循环运行期间调用。
        对 aiomysql 安全 —— 使用当前事件循环，不会创建新循环。
        """
        try:
            runtime_config = await _read_config_async("scheduler_runtime_config")
            if runtime_config:
                field_map = {
                    "ci_sync_interval_minutes": "CI_SYNC_INTERVAL_MINUTES",
                    "ci_sync_days_back": "CI_SYNC_DAYS_BACK",
                    "ci_sync_max_runs_per_workflow": "CI_SYNC_MAX_RUNS_PER_WORKFLOW",
                    "ci_sync_force_full_refresh": "CI_SYNC_FORCE_FULL_REFRESH",
                    "ci_auto_failure_analysis_enabled": "CI_AUTO_FAILURE_ANALYSIS_ENABLED",
                    "model_sync_interval_minutes": "MODEL_SYNC_INTERVAL_MINUTES",
                    "model_sync_days_back": "MODEL_SYNC_DAYS_BACK",
                    "model_sync_runs_limit": "MODEL_SYNC_RUNS_LIMIT",
                    "pr_pipeline_sync_interval_minutes": "PR_PIPELINE_SYNC_INTERVAL_MINUTES",
                    "pr_pipeline_days_back": "PR_PIPELINE_DAYS_BACK",
                    "pr_pipeline_max_items_per_sync": "PR_PIPELINE_MAX_ITEMS_PER_SYNC",
                    "pr_pipeline_incremental_lookback_minutes": "PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES",
                    "project_dashboard_cache_interval_minutes": "PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES",
                    "data_retention_days": "DATA_RETENTION_DAYS",
                    "github_cache_dir": "GITHUB_CACHE_DIR",
                }
                for config_key, setting_name in field_map.items():
                    if config_key in runtime_config:
                        setattr(settings, setting_name, runtime_config[config_key])

                # Older database configuration may still contain the former
                # hourly value. Do not let it restore per-analysis-era churn.
                settings.PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES = max(
                    int(settings.PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES),
                    1440,
                )

                self.scheduler.add_job(
                    self._sync_ci_data_job,
                    trigger=IntervalTrigger(minutes=settings.CI_SYNC_INTERVAL_MINUTES),
                    id="ci_data_sync",
                    name="CI Data Sync",
                    replace_existing=True,
                )
                self.scheduler.add_job(
                    self._update_project_dashboard_cache_job,
                    trigger=CronTrigger(hour=4, minute=30, timezone=self._timezone),
                    id="project_dashboard_cache_update",
                    name="Repository Mirror Refresh",
                    replace_existing=True,
                )
                self.scheduler.add_job(
                    self._sync_model_reports_job,
                    trigger=IntervalTrigger(minutes=settings.MODEL_SYNC_INTERVAL_MINUTES),
                    id="model_report_sync",
                    name="Model Report Sync",
                    replace_existing=True,
                )
                self.scheduler.add_job(
                    self._sync_pr_pipeline_job,
                    trigger=IntervalTrigger(minutes=settings.PR_PIPELINE_SYNC_INTERVAL_MINUTES),
                    id="pr_pipeline_sync",
                    name="PR Pipeline Sync",
                    replace_existing=True,
                )
        except Exception as e:
            logger.warning("Failed to apply scheduler runtime config: %s", e)

        try:
            schedule_config = await _read_config_async('daily_summary_schedule')
            if schedule_config:
                self._timezone = schedule_config.get('timezone', 'Asia/Shanghai')
                # 更新时区（需要重建 cron 任务）
                if self._timezone != 'Asia/Shanghai':
                    self.scheduler.configure(timezone=self._timezone)
                    logger.info(f"Applied timezone from DB: {self._timezone}")
        except Exception as e:
            logger.warning(f"Failed to apply DB timezone: {e}")

        try:
            report_config = await _read_config_async('daily_report_config')
            if report_config:
                hour = report_config.get('report_schedule_hour')
                minute = report_config.get('report_schedule_minute')
                if hour is not None and minute is not None:
                    self.update_report_schedule(
                        enabled=report_config.get('report_enabled', True),
                        cron_hour=int(hour),
                        cron_minute=int(minute),
                    )
                    logger.info(f"Applied report schedule from DB: {hour}:{minute:02d}")
        except Exception as e:
            logger.warning(f"Failed to apply DB report schedule: {e}")

        try:
            metrics_config = await _read_config_async('resource_metrics_config')
            if metrics_config and 'interval_minutes' in metrics_config:
                self.update_resource_metrics_schedule(int(metrics_config['interval_minutes']))
        except Exception as e:
            logger.warning(f"Failed to apply DB metrics interval: {e}")

    async def _consume_control_events(self) -> None:
        """Acknowledge durable API commands, then reload this process's schedule."""
        try:
            async with SessionLocal() as db:
                async with db.begin():
                    result = await db.execute(
                        text(
                            """
                            SELECT event_id FROM control_outbox
                            WHERE processed_at IS NULL
                              AND aggregate_type = 'scheduler'
                              AND event_type = 'scheduler.config.reload'
                            ORDER BY created_at
                            LIMIT 25 FOR UPDATE SKIP LOCKED
                            """
                        )
                    )
                    event_ids = [row[0] for row in result.fetchall()]
                    if event_ids:
                        await db.execute(
                            text(
                                "UPDATE control_outbox SET processed_at = NOW() "
                                "WHERE event_id IN :event_ids"
                            ).bindparams(bindparam("event_ids", expanding=True)),
                            {"event_ids": event_ids},
                        )
            if event_ids:
                await self.apply_db_config_overrides()
                logger.info("Applied %d scheduler control-plane events", len(event_ids))
        except Exception as e:
            logger.warning("Scheduler control-plane poll failed: %s", e)

    def update_report_schedule(
        self, enabled: bool = True, cron_hour: int = 8, cron_minute: int = 30
    ):
        """动态更新每日报告邮件定时任务"""
        try:
            if enabled:
                self.scheduler.add_job(
                    self._send_daily_report_job,
                    trigger=CronTrigger(hour=cron_hour, minute=cron_minute, timezone=self.scheduler.timezone),
                    id="daily_report_task",
                    name="Daily Report Email",
                    replace_existing=True,
                )
                logger.info(f"Daily report schedule updated: {cron_hour}:{cron_minute:02d}")
            else:
                try:
                    self.scheduler.remove_job('daily_report_task')
                except Exception:
                    pass
                logger.info("Daily report task disabled")
        except Exception as e:
            logger.error(f"Failed to update daily report schedule: {e}", exc_info=True)

    # /status 接口向用户展示的 job id —— 心跳需快照这些 job 的 next_run_time
    _HEARTBEAT_TRACKED_JOBS = (
        "ci_data_sync",
        "model_report_sync",
        "project_dashboard_cache_update",
        "daily_summary_task",
        "coverage_sync",
    )

    async def write_heartbeat(self, *, force_running: bool | None = None) -> None:
        """将当前调度器状态写入 DB 心跳表，供 API 进程读取。

        独立 scheduler 容器每 20s 调用一次；关闭前以 force_running=False 再调一次，
        使 API 进程能立即感知调度器已停止（无需等待心跳过期）。
        """
        import os

        from infrastructure.persistence.models import SchedulerHeartbeat

        running = bool(self.scheduler.running) if force_running is None else force_running
        jobs_payload: dict[str, dict] = {}
        for job_id in self._HEARTBEAT_TRACKED_JOBS:
            job = self.scheduler.get_job(job_id)
            jobs_payload[job_id] = {
                "name": job.name if job else None,
                "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
            }

        try:
            async with SessionLocal() as db:
                row = await db.get(SchedulerHeartbeat, 1)
                if row is None:
                    row = SchedulerHeartbeat(id=1)
                    db.add(row)
                row.running = running
                row.jobs = jobs_payload
                row.pid = os.getpid()
                row.updated_at = datetime.now(UTC)
                await db.commit()
        except Exception as e:
            # 心跳失败不应影响调度器主循环（如 DB 短暂抖动、表尚未由 backend init_db 建好）
            logger.warning(f"Heartbeat write failed (non-fatal): {e}")

    def stop(self) -> None:
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("DataSyncScheduler stopped")

        # 注意：这里不能直接 await 关闭 GitHub 客户端
        # 需要在应用生命周期管理中使用 async close() 方法
        if self.github_client:
            logger.info("GitHub client will be closed on next cleanup")

    async def close(self) -> None:
        """关闭调度器并清理资源（异步版本）"""
        # 关闭前写入最终心跳（running=False），让 API 进程立即感知调度器已停止
        try:
            await self.write_heartbeat(force_running=False)
        except Exception as e:
            logger.warning(f"Final heartbeat write failed (non-fatal): {e}")
        self.stop()
        if self.github_client:
            await self.github_client.close()
            self.github_client = None
            self._initialized = False
        logger.info("DataSyncScheduler resources cleaned up")

    def _initialize_github_client(self) -> None:
        """初始化 GitHub 客户端"""
        if not settings.GITHUB_TOKEN:
            logger.warning("GITHUB_TOKEN not configured, GitHub API calls will fail")

        self.github_client = GitHubClient(
            token=settings.GITHUB_TOKEN,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO,
        )
        self._initialized = True

    async def _sync_npu_occupancy_job(self) -> None:
        from resource_dashboard.npu_occupancy_repository import NpuOccupancyRepository

        end = datetime.now(UTC)
        start = end - timedelta(hours=settings.NPU_OCCUPANCY_SYNC_LOOKBACK_HOURS)
        result = await NpuOccupancyRepository().sync(start, end)
        logger.info("NPU occupancy sync result: %s", result)

    async def _sync_nightly_data_job(self) -> None:
        """Queue Nightly YAML snapshot and failure materialization."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"nightly_data_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "nightly_data_sync",
                {},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued Nightly data synchronization task %d", task_id)

    async def _sync_ci_data_job(self) -> None:
        """Queue CI synchronization; Collector owns GitHub I/O and all writes."""
        from infrastructure.tasks.task_manager import TaskManager

        params = {
            "days_back": settings.CI_SYNC_DAYS_BACK,
            "max_runs": settings.CI_SYNC_MAX_RUNS_PER_WORKFLOW,
            "force_full_refresh": settings.CI_SYNC_FORCE_FULL_REFRESH,
        }
        # A sync can take longer than its schedule interval (artifact caching
        # is intentionally part of the run). Share one active key with manual
        # sync requests so we never queue an overlapping CI collection.
        dedupe_key = "ci_sync:active"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "ci_sync",
                params,
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued CI synchronization task %d", task_id)
        else:
            logger.info("CI synchronization is already queued or running")

    async def _sync_pr_pipeline_job(self) -> None:
        """Queue PR synchronization; Collector owns the GitHub I/O and mutation."""
        from infrastructure.tasks.task_manager import TaskManager

        days_back = getattr(settings, "PR_PIPELINE_DAYS_BACK", 7)
        max_items = getattr(settings, "PR_PIPELINE_MAX_ITEMS_PER_SYNC", 50)
        lookback_minutes = getattr(settings, "PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES", 15)
        # A stable one-minute window suppresses duplicate enqueues after a
        # Scheduler restart without preventing the next scheduled run.
        dedupe_key = f"pr_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "pr_sync",
                {
                    "days_back": days_back,
                    "incremental": True,
                    "max_items": max_items,
                    "lookback_minutes": lookback_minutes,
                },
                dedupe_key,
                required_capability="python",
            )
            await db.commit()

        if task_id:
            logger.info("Queued PR pipeline sync task %d", task_id)
        else:
            logger.info("PR pipeline sync is already queued or running")

    async def _cleanup_logs_job(self) -> None:
        logger.info("LOG CLEANUP JOB STARTED")
        retention_days = getattr(settings, 'DATA_RETENTION_DAYS', 365)
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with SessionLocal() as db:
            try:
                from sqlalchemy import delete as sa_delete

                from infrastructure.persistence.models import (
                    FeatureUsageLog,
                    TokenBlacklist,
                    UserLoginLog,
                )
                lr = await db.execute(sa_delete(UserLoginLog).where(UserLoginLog.login_time < cutoff))
                ur = await db.execute(sa_delete(FeatureUsageLog).where(FeatureUsageLog.access_time < cutoff))
                tr = await db.execute(sa_delete(TokenBlacklist).where(TokenBlacklist.expires_at < datetime.now(UTC)))
                await db.commit()
                logger.info(f"LOG CLEANUP: login={lr.rowcount}, usage={ur.rowcount}, tokens={tr.rowcount}")
            except Exception as e:
                logger.error(f"LOG CLEANUP FAILED: {e}", exc_info=True)
                await db.rollback()

    async def _update_project_dashboard_cache_job(self) -> None:
        """Queue one daily mirror refresh for every active Collector node."""
        from infrastructure.tasks.task_manager import TaskManager

        today = datetime.now(UTC).strftime('%Y-%m-%d')
        async with SessionLocal() as db:
            node_ids = (
                await db.execute(
                    text(
                        """
                        SELECT node_id
                        FROM collector_heartbeats
                        WHERE running = 1
                          AND updated_at >= NOW() - INTERVAL 2 MINUTE
                        """
                    )
                )
            ).scalars().all()
            targets = sorted({str(node_id) for node_id in node_ids if node_id}) or [""]
            task_ids = []
            for node_id in targets:
                task_id = await TaskManager.create_task(
                    db,
                    "repo_cache_refresh",
                    {"repo_type": "all", "target_node": node_id or None},
                    f"repo_cache_refresh:scheduled:{today}:{node_id or 'fallback'}",
                    required_capability=f"node:{node_id}" if node_id else "python",
                )
                if task_id:
                    task_ids.append(task_id)
            await db.commit()
        logger.info("Repository mirror refresh tasks queued: %s", task_ids)

    async def _sync_model_reports_job(self) -> None:
        """Queue model report synchronization for a Collector with Python capability."""
        from infrastructure.tasks.task_manager import TaskManager

        params = {
            "days_back": getattr(settings, "MODEL_SYNC_DAYS_BACK", 3),
            "runs_limit": getattr(settings, "MODEL_SYNC_RUNS_LIMIT", 100),
        }
        dedupe_key = f"model_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "model_sync",
                params,
                dedupe_key,
                required_capability="python",
            )
            await db.commit()

        if task_id:
            logger.info("Queued model report sync task %d", task_id)
        else:
            logger.info("Model report sync is already queued or running")

    async def _generate_daily_summary_job(self):
        """每日总结生成任务"""
        logger.info("=" * 60)
        logger.info("DAILY SUMMARY GENERATION JOB STARTED")
        logger.info("=" * 60)

        try:
            from datetime import timedelta

            from sqlalchemy import select

            from infrastructure.persistence.models import ProjectDashboardConfig
            from reporting.daily_report import _today_shanghai
            from reporting.daily_summary import DailySummaryService

            # 创建数据库会话
            # Reuse the scheduler process' configured engine and pool instead
            # of creating an untracked pool for every scheduled run.
            async with SessionLocal() as db:
                # 获取配置的项目列表
                projects_stmt = select(ProjectDashboardConfig).where(
                    ProjectDashboardConfig.config_key == 'daily_summary_projects'
                )
                projects_result = await db.execute(projects_stmt)
                projects_config = projects_result.scalar_one_or_none()

                projects = projects_config.config_value if projects_config else [
                    {"id": "ascend", "name": "vLLM Ascend", "enabled": True},
                    {"id": "vllm", "name": "vLLM", "enabled": True},
                ]

                # 计算昨天的日期
                yesterday = _today_shanghai() - timedelta(days=1)

                for project in projects:
                    if not project.get("enabled", True):
                        continue

                    project_id = project.get("id")
                    if not project_id:
                        continue

                    try:
                        # 1. 获取数据
                        logger.info(f"Fetching data for project: {project_id} on {yesterday}")
                        service = DailySummaryService(db)
                        await service.fetch_daily_data(project_id, yesterday)

                        # 2. 生成总结
                        logger.info(f"Generating summary for project: {project_id} on {yesterday}")
                        await service.generate_summary(project_id, yesterday)

                        logger.info(f"Daily summary completed for project: {project_id}")
                    except Exception as e:
                        logger.error(f"Failed to generate summary for {project_id}: {e}", exc_info=True)

                await db.commit()

            logger.info("=" * 60)
            logger.info("DAILY SUMMARY GENERATION JOB COMPLETED")
            logger.info("=" * 60)

        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"DAILY SUMMARY GENERATION JOB FAILED - Error: {e}", exc_info=True)
            logger.error("=" * 60)

    async def _send_daily_report_job(self):
        """每日运行报告邮件推送任务"""
        logger.info("=" * 60)
        logger.info("DAILY REPORT EMAIL JOB STARTED")
        logger.info("=" * 60)

        try:
            from datetime import timedelta

            from sqlalchemy import select

            from infrastructure.persistence.models import ProjectDashboardConfig
            from reporting.daily_report import (
                REPORT_CONFIG_KEY,
                DailyReportService,
                _today_shanghai,
            )

            if not settings.REPORT_ENABLED:
                logger.info("Report disabled, skipping")
                return

            # Reuse the scheduler process' configured engine and pool for the
            # report job as well.
            async with SessionLocal() as db:
                # 读报告配置
                stmt = select(ProjectDashboardConfig).where(
                    ProjectDashboardConfig.config_key == REPORT_CONFIG_KEY
                )
                config_result = await db.execute(stmt)
                config = config_result.scalar_one_or_none()
                db_config = config.config_value if config else {}

                # SMTP 配置在单独的 config_key 里
                smtp_stmt = select(ProjectDashboardConfig).where(
                    ProjectDashboardConfig.config_key == "smtp_config"
                )
                smtp_result = await db.execute(smtp_stmt)
                smtp_config = smtp_result.scalar_one_or_none()
                smtp_values = smtp_config.config_value if smtp_config else {}

                if not db_config.get("report_recipients"):
                    logger.info("No recipients configured in DB, skipping")
                    return

                if not smtp_values.get("smtp_host"):
                    logger.info("SMTP_HOST not configured in smtp_config, skipping")
                    return

                yesterday = _today_shanghai() - timedelta(days=1)
                service = DailyReportService(db)
                history = await service.send_report(yesterday)

                logger.info(f"Daily report result: status={history.status}, date={history.report_date}")

            logger.info("=" * 60)
            logger.info("DAILY REPORT EMAIL JOB COMPLETED")
            logger.info("=" * 60)

        except Exception as e:
            logger.error("=" * 60)
            logger.error(f"DAILY REPORT EMAIL JOB FAILED - Error: {e}", exc_info=True)
            logger.error("=" * 60)
            raise  # Fix #135: re-raise so APScheduler records the failure

    async def _collect_resource_metrics_job(self) -> None:
        """Queue resource metric collection for Collector execution."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"resource_metrics_collect:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "resource_metrics_collect",
                {},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued resource metrics collection task %d", task_id)

    async def _collect_npu_queue_snapshot_job(self) -> None:
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"npu_queue_snapshot:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db, "npu_queue_snapshot", {}, dedupe_key, required_capability="python"
            )
            await db.commit()
        if task_id:
            logger.info("Queued NPU queue snapshot task %d", task_id)

    async def _cleanup_resource_metrics_job(self) -> None:
        """Queue resource metric retention cleanup for Collector execution."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"resource_metrics_cleanup:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "resource_metrics_cleanup",
                {},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued resource metrics cleanup task %d", task_id)

    async def _cleanup_npu_queue_snapshots_job(self) -> None:
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"npu_queue_snapshot_cleanup:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db, "npu_queue_snapshot_cleanup", {}, dedupe_key, required_capability="python"
            )
            await db.commit()
        if task_id:
            logger.info("Queued NPU queue snapshot cleanup task %d", task_id)

    async def _parse_test_results_job(self) -> None:
        """Queue test-board parsing; Collector owns GitHub I/O and writes."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"test_board_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "test_board_sync",
                {"days_back": 7},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued test-board sync task %d", task_id)

    async def _sync_coverage_job(self) -> None:
        """Queue coverage synchronization; the Collector owns external I/O."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"coverage_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "coverage_sync",
                {"source": "all", "strategy": "hourly"},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued coverage sync task %d", task_id)

    async def _sync_coverage_daily_baseline_job(self) -> None:
        """Queue the daily, independently retained coverage source snapshot."""
        from infrastructure.tasks.task_manager import TaskManager

        day = datetime.now(UTC).strftime("%Y-%m-%d")
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "coverage_sync",
                {"source": "all", "strategy": "daily_baseline"},
                f"coverage_sync:daily-baseline:{day}",
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued daily coverage baseline task %d", task_id)
    async def _calc_test_health_job(self) -> None:
        logger.info("TEST BOARD HEALTH CALC JOB STARTED")
        async with SessionLocal() as db:
            try:
                from tooling.analytics.test_health_calculator import TestHealthCalculator
                calc = TestHealthCalculator(db)
                count = await calc.calculate_all_health_scores()
                logger.info(f"TEST BOARD HEALTH CALC JOB COMPLETED - {count} cases updated")
            except Exception as e:
                logger.error(f"TEST BOARD HEALTH CALC JOB FAILED: {e}", exc_info=True)

    async def _snapshot_test_suites_job(self) -> None:
        logger.info("TEST BOARD SUITE SNAPSHOT JOB STARTED")
        async with SessionLocal() as db:
            try:
                from tooling.analytics.test_health_calculator import TestHealthCalculator
                calc = TestHealthCalculator(db)
                count = await calc.calculate_suite_snapshot()
                logger.info(f"TEST BOARD SUITE SNAPSHOT JOB COMPLETED - {count} snapshots")
            except Exception as e:
                logger.error(f"TEST BOARD SUITE SNAPSHOT JOB FAILED: {e}", exc_info=True)

    async def _cleanup_test_runs_job(self) -> None:
        logger.info("TEST BOARD RUN CLEANUP JOB STARTED")
        async with SessionLocal() as db:
            try:
                from tooling.analytics.test_health_calculator import TestHealthCalculator
                calc = TestHealthCalculator(db)
                deleted = await calc.cleanup_old_test_runs()
                logger.info(f"TEST BOARD RUN CLEANUP JOB COMPLETED - {deleted} records deleted")
            except Exception as e:
                logger.error(f"TEST BOARD RUN CLEANUP JOB FAILED: {e}", exc_info=True)

    async def _cleanup_stale_cases_job(self) -> None:
        """物理清理超过 STALE_CASE_DELETE_DAYS 天未运行的已退出测试用例。"""
        logger.info("TEST BOARD STALE CASE CLEANUP JOB STARTED")
        async with SessionLocal() as db:
            try:
                from tooling.analytics.test_health_calculator import TestHealthCalculator
                calc = TestHealthCalculator(db)
                deleted = await calc.cleanup_stale_cases()
                logger.info(f"TEST BOARD STALE CASE CLEANUP JOB COMPLETED - {deleted} stale cases deleted")
            except Exception as e:
                logger.error(f"TEST BOARD STALE CASE CLEANUP JOB FAILED: {e}", exc_info=True)

    def update_daily_summary_schedule(self, enabled: bool, cron_hour: int, cron_minute: int, timezone: str = 'Asia/Shanghai'):
        """
        动态更新每日总结定时任务配置

        Args:
            enabled: 是否启用
            cron_hour: 执行时间（小时）
            cron_minute: 执行时间（分钟）
            timezone: 时区
        """
        try:
            if enabled:
                self.scheduler.add_job(
                    self._generate_daily_summary_job,
                    trigger=CronTrigger(hour=cron_hour, minute=cron_minute, timezone=timezone),
                    id="daily_summary_task",
                    name="Generate Daily Summary",
                    replace_existing=True,
                )
                logger.info(f"Daily summary schedule updated: {cron_hour}:{cron_minute:02d} {timezone}")
            else:
                try:
                    self.scheduler.remove_job('daily_summary_task')
                except Exception:
                    pass  # 任务可能不存在，忽略错误
                logger.info("Daily summary task disabled")
        except Exception as e:
            logger.error(f"Failed to update daily summary schedule: {e}", exc_info=True)

    def update_resource_metrics_schedule(self, interval_minutes: int = 1):
        """
        动态更新 NPU 指标采集间隔

        Args:
            interval_minutes: 采集间隔（分钟）
        """
        try:
            self.scheduler.add_job(
                self._collect_resource_metrics_job,
                trigger=IntervalTrigger(minutes=interval_minutes),
                id="resource_metrics_collect",
                name="Resource Metrics Collect",
                replace_existing=True,
            )
            logger.info(f"Resource metrics collection schedule updated: every {interval_minutes} minutes")
        except Exception as e:
            logger.error(f"Failed to update resource metrics schedule: {e}", exc_info=True)

    async def trigger_manual_sync(
        self,
        sync_type: str = "ci",
        days_back: int = 7,
        max_runs_per_workflow: int = 100,
        force_full_refresh: bool = False,
    ) -> dict:
        """
        手动触发同步

        Args:
            sync_type: 同步类型 ("ci")
            days_back: 从多少天前开始采集
            max_runs_per_workflow: 每个 workflow 最多采集多少条记录
            force_full_refresh: 是否强制全量覆盖刷新

        Returns:
            同步结果信息
        """
        logger.info(f"Manual sync triggered: {sync_type}, days_back={days_back}, max_runs={max_runs_per_workflow}, force={force_full_refresh}")

        if sync_type != "ci":
            return {
                "success": False,
                "message": f"Unsupported sync type: {sync_type}",
            }

        from infrastructure.tasks.task_manager import TaskManager

        params = {
            "days_back": days_back,
            "max_runs": max_runs_per_workflow,
            "force_full_refresh": force_full_refresh,
        }
        dedupe_key = "ci_sync:active"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "ci_sync",
                params,
                dedupe_key,
                required_capability="python",
            )
            await db.commit()

        return {
            "success": True,
            "message": "CI synchronization task queued for Collector execution",
            "task_id": task_id,
            "queued": task_id is not None,
        }

    def get_next_run_time(self, job_id: str) -> datetime | None:
        """
        获取任务下次执行时间

        Args:
            job_id: 任务 ID

        Returns:
            下次执行时间，任务不存在时返回 None
        """
        job = self.scheduler.get_job(job_id)
        if job:
            return job.next_run_time
        return None

    def get_job_info(self) -> list[dict]:
        """
        获取所有任务信息

        Returns:
            任务信息列表
        """
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return jobs

    async def _sync_support_matrix_job(self) -> None:
        """Queue support-matrix synchronization for Collector execution."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"support_matrix_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "support_matrix_sync",
                {"dry_run": False},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued support-matrix sync task %d", task_id)
    async def _cleanup_code_metrics_job(self):
        """定时清理过期代码度量明细数据（365天保留）"""
        try:
            from sqlalchemy import delete, select

            from infrastructure.persistence.models import (
                CodeComplexityDetail,
                CodeDuplicationDetail,
                CodeMetricsSnapshot,
                CodeSecurityDetail,
            )
            cutoff_date = date.today() - timedelta(days=365)
            async with SessionLocal() as db:
                old_ids = [row[0] for row in (await db.execute(
                    select(CodeMetricsSnapshot.id).where(CodeMetricsSnapshot.snapshot_date < cutoff_date)
                ))]
                if old_ids:
                    await db.execute(delete(CodeComplexityDetail).where(CodeComplexityDetail.snapshot_id.in_(old_ids)))
                    await db.execute(delete(CodeDuplicationDetail).where(CodeDuplicationDetail.snapshot_id.in_(old_ids)))
                    await db.execute(delete(CodeSecurityDetail).where(CodeSecurityDetail.snapshot_id.in_(old_ids)))
                    await db.execute(delete(CodeMetricsSnapshot).where(CodeMetricsSnapshot.id.in_(old_ids)))
                    await db.commit()
                    logger.info(f"Code metrics cleanup: deleted {len(old_ids)} expired snapshots")
        except Exception as e:
            logger.error(f"Code metrics cleanup failed: {e}")

    async def _sync_heatmap_job(self):
        """Queue GitHub heatmap synchronization for Collector execution."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"code_heatmap_sync:scheduled:{datetime.now(UTC).strftime('%Y-%m-%d')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "code_heatmap_sync",
                {"days": 30},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued code heatmap synchronization task %d", task_id)

    async def _collect_code_metrics_job(self):
        """Queue code metrics collection; only Collector may run local tools."""
        from infrastructure.tasks.task_manager import TaskManager

        dedupe_key = f"code_metrics:scheduled:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}"
        async with SessionLocal() as db:
            task_id = await TaskManager.create_task(
                db,
                "code_metrics_collect",
                {"branch": "main"},
                dedupe_key,
                required_capability="python",
            )
            await db.commit()
        if task_id:
            logger.info("Queued code metrics collection task %d", task_id)

# 全局调度器实例
_scheduler: DataSyncScheduler | None = None


def get_scheduler() -> DataSyncScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = DataSyncScheduler()
    return _scheduler


def start_scheduler() -> None:
    """启动全局调度器（同步部分：注册默认时间的所有任务）"""
    scheduler = get_scheduler()
    scheduler.start()


async def start_scheduler_async() -> None:
    """启动全局调度器并加载 DB 配置覆盖"""
    from infrastructure.core.github_config import load_github_runtime_config

    await load_github_runtime_config()
    scheduler = get_scheduler()
    scheduler.start()
    await scheduler.apply_db_config_overrides()
    await scheduler.write_heartbeat()


async def stop_scheduler_async() -> None:
    """停止全局调度器并清理资源（异步版本）"""
    global _scheduler
    if _scheduler:
        await _scheduler.close()
        _scheduler = None


def stop_scheduler() -> None:
    """停止全局调度器（同步版本，不关闭 GitHub 客户端）"""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        # 注意：同步版本无法关闭异步的 GitHub 客户端
        # 建议使用 stop_scheduler_async
