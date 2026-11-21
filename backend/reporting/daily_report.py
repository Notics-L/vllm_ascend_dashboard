"""
每日运行报告邮件推送服务
聚合三个时间窗口数据，构建 HTML 邮件并发送

SMTP 配置存储在数据库（ProjectDashboardConfig 表，config_key='smtp_config'）
报告推送配置存储在 config_key='daily_report_config'
与 LLMProviderConfig 设计一致：敏感凭据不放在 .env 文件中
"""
import base64
import json
import logging
from datetime import UTC, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    CIResult,
    DailyReportHistory,
    ModelConfig,
    ModelReport,
    ProjectDashboardConfig,
)
from infrastructure.persistence.models.test_board import TestCase, TestRun
from infrastructure.storage.daily_data_file_store import DailyDataFileStore
from notifications.service import NotificationDispatcher

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TEMPLATES_DIR = Path(__file__).parents[1] / "templates"

REPORT_CONFIG_KEY = "daily_report_config"


class DailyReportService:
    """每日运行报告服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.file_store = DailyDataFileStore()

    async def _get_report_config(self) -> dict:
        """从数据库读取报告邮件配置（ProjectDashboardConfig 表）"""
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == REPORT_CONFIG_KEY
        )
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()

        if config and config.config_value:
            return config.config_value

        return {}

    async def _update_report_config(self, config_value: dict) -> None:
        """更新数据库中的报告邮件配置"""
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == REPORT_CONFIG_KEY
        )
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()

        if config:
            config.config_value = config_value
        else:
            config = ProjectDashboardConfig(
                config_key=REPORT_CONFIG_KEY,
                config_value=config_value,
                description="每日运行报告邮件推送配置（SMTP、收件人等）",
            )
            self.db.add(config)

        await self.db.flush()

    async def generate_report(self, report_date: date) -> dict:
        """
        生成完整报告数据（三个时间窗口）

        Args:
            report_date: 报告日期（昨日日期）

        Returns:
            报告数据字典，包含 yesterday/last_week/last_month 三个窗口
        """
        yesterday_start = report_date
        yesterday_end = report_date
        last_week_start = report_date - timedelta(days=6)
        last_month_start = report_date - timedelta(days=29)

        windows = {
            "yesterday": (yesterday_start, yesterday_end),
            "last_week": (last_week_start, yesterday_end),
            "last_month": (last_month_start, yesterday_end),
        }

        report_data = {}
        for window_key, (start, end) in windows.items():
            start_dt = _date_to_utc_start(start)
            end_dt = _date_to_utc_end(end)

            ci_data = await self._collect_ci_data(start_dt, end_dt)
            model_data = await self._collect_model_data(start_dt, end_dt)
            gh_data = await self._collect_github_data(start, end, window_key)
            perf_data = await self._collect_perf_data(start_dt, end_dt)

            report_data[window_key] = {
                "ci": ci_data,
                "model": model_data,
                "github": gh_data,
                "performance": perf_data,
            }

            if window_key == "yesterday":
                report_data[window_key]["resource"] = await self._collect_resource_data(start_dt, end_dt)
                report_data[window_key]["test"] = await self._collect_test_data(start_dt, end_dt)
                report_data[window_key]["pr_pipeline"] = await self._collect_pr_pipeline_data(start_dt, end_dt)
                report_data[window_key]["diagnosis_stats"] = await self._collect_diagnosis_stats(start_dt, end_dt)

        return {
            "report_date": report_date.isoformat(),
            "timezone": "Asia/Shanghai",
            "report_window": {
                "start": f"{report_date.isoformat()}T00:00:00+08:00",
                "end": f"{report_date.isoformat()}T23:59:59.999999+08:00",
            },
            **report_data,
        }

    async def _collect_ci_data(self, start_dt, end_dt) -> dict:
        """Collect executed test-case results from the Nightly A2/A3 workflows."""
        stmt = (
            select(TestRun, TestCase.data_granularity)
            .join(TestCase, TestRun.test_case_id == TestCase.id)
            .where(
                TestRun.started_at >= start_dt,
                TestRun.started_at <= end_dt,
                TestRun.workflow_name.isnot(None),
            )
        )
        result = await self.db.execute(stmt)
        nightly_cases = []
        job_level_fallback_runs = 0
        duplicate_runs = 0
        seen_run_keys = set()
        for run, data_granularity in result.all():
            workflow = run.workflow_name.lower().replace("_", "-").replace(" ", "-")
            hardware = "A2" if "a2" in workflow else "A3" if "a3" in workflow else None
            if "nightly" not in workflow or hardware is None:
                continue
            normalized_result = (run.result or "").lower()
            if normalized_result not in {"passed", "pass", "success", "failed", "fail", "failure", "error"}:
                continue
            if data_granularity == "job_level":
                job_level_fallback_runs += 1
                continue
            run_key = (run.ci_job_id, run.test_case_id, run.started_at, normalized_result)
            if run_key in seen_run_keys:
                duplicate_runs += 1
                continue
            seen_run_keys.add(run_key)
            nightly_cases.append((run, hardware, normalized_result))

        total = len(nightly_cases)
        passed = sum(1 for _, _, value in nightly_cases if value in {"passed", "pass", "success"})
        failed = total - passed
        rate = (passed / total * 100) if total else 0.0
        durations = [run.duration_seconds for run, _, _ in nightly_cases if run.duration_seconds is not None]
        avg_dur = sum(durations) / len(durations) if durations else None

        hardware_stats = []
        for hardware in ("A2", "A3"):
            cases = [item for item in nightly_cases if item[1] == hardware]
            hardware_passed = sum(1 for _, _, value in cases if value in {"passed", "pass", "success"})
            hardware_stats.append({
                "hardware": hardware,
                "total_cases": len(cases),
                "passed_cases": hardware_passed,
                "failed_cases": len(cases) - hardware_passed,
                "pass_rate": (hardware_passed / len(cases) * 100) if cases else 0.0,
            })

        failed_workflows = [
            {
                "workflow_name": f"Nightly {item['hardware']}",
                "hardware": item["hardware"],
                "total_cases": item["total_cases"],
                "failed_cases": item["failed_cases"],
                "pass_rate": item["pass_rate"],
            }
            for item in hardware_stats
            if item["failed_cases"] > 0
        ]

        return {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "pass_rate": rate,
            "by_hardware": hardware_stats,
            # Compatibility aliases for existing charts/templates and stored reports.
            "total_runs": total,
            "success_runs": passed,
            "failure_runs": failed,
            "success_rate": rate,
            "avg_duration_seconds": avg_dur,
            "failed_workflows": failed_workflows,
            "excluded_job_level_runs": job_level_fallback_runs,
            "deduplicated_runs": duplicate_runs,
        }

    async def _collect_model_data(self, start_dt, end_dt) -> dict:
        """采集模型验证概况"""
        report_time = func.coalesce(CIResult.started_at, CIResult.completed_at, ModelReport.created_at)
        stmt = (
            select(ModelReport)
            .outerjoin(CIResult, ModelReport.workflow_run_id == CIResult.run_id)
            .where(
                report_time >= start_dt,
                report_time <= end_dt,
            )
        )
        result = await self.db.execute(stmt)
        reports = result.scalars().all()

        total = len(reports)
        pass_count = sum(1 for r in reports if _is_model_pass(r.pass_fail))
        fail_count = sum(1 for r in reports if _is_model_fail(r.pass_fail))
        rate = (pass_count / total * 100) if total > 0 else 0.0

        model_ids_in_window = {r.model_config_id for r in reports}

        earlier_stmt = (
            select(ModelReport)
            .outerjoin(CIResult, ModelReport.workflow_run_id == CIResult.run_id)
            .where(report_time < start_dt)
        )
        earlier_result = await self.db.execute(earlier_stmt)
        earlier_ids = {r.model_config_id for r in earlier_result.scalars().all()}

        new_model_ids = model_ids_in_window - earlier_ids
        new_models = []
        if new_model_ids:
            mc_stmt = select(ModelConfig).where(ModelConfig.id.in_(new_model_ids))
            mc_result = await self.db.execute(mc_stmt)
            new_models = [mc.model_name for mc in mc_result.scalars().all()]

        failed_models = []
        for r in reports:
            if _is_model_fail(r.pass_fail):
                mc_stmt2 = select(ModelConfig).where(ModelConfig.id == r.model_config_id)
                mc2 = (await self.db.execute(mc_stmt2)).scalar_one_or_none()
                failed_models.append({
                    "model_name": mc2.model_name if mc2 else f"ID:{r.model_config_id}",
                    "hardware": r.hardware,
                    "vllm_version": r.vllm_version,
                })

        return {
            "total_reports": total,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_rate": rate,
            "new_models": new_models,
            "failed_models": failed_models,
        }

    async def _collect_github_data(self, start: date, end: date, window_key: str) -> dict:
        """采集 GitHub 活动概况"""
        project = "ascend"
        pr_count = 0
        issue_count = 0
        commit_count = 0
        ai_snippet = None
        recent_prs = []
        recent_issues = []
        recent_commits = []

        current_date = end
        while current_date >= start:
            data = await self.file_store.load_daily_data(project, current_date)
            if data:
                counts = data.get("counts", {})
                pull_requests = data.get("pull_requests", [])
                issues = data.get("issues", [])
                commits = data.get("commits", [])
                pr_count += counts.get("prs", len(pull_requests))
                issue_count += counts.get("issues", len(issues))
                commit_count += counts.get("commits", len(commits))
                recent_prs.extend(_compact_pr(item) for item in pull_requests[:20])
                recent_issues.extend(_compact_issue(item) for item in issues[:20])
                recent_commits.extend(_compact_commit(item) for item in commits[:30])
            current_date -= timedelta(days=1)

        if window_key == "yesterday":
            summary = await self.file_store.load_summary(project, start)
            if summary and summary.get("summary_markdown"):
                md = summary["summary_markdown"]
                lines = md.split("\n")
                snippet_lines = [line for line in lines[:8] if line.strip()]
                ai_snippet = " ".join(snippet_lines)[:300]

        return {
            "pr_count": pr_count,
            "issue_count": issue_count,
            "commit_count": commit_count,
            "ai_summary_snippet": ai_snippet,
            "recent_prs": recent_prs[:30],
            "recent_issues": recent_issues[:30],
            "recent_commits": recent_commits[:40],
        }

    async def _collect_perf_data(self, start_dt, end_dt) -> dict:
        """采集性能指标概况（从 ModelReport.metrics_json 提取，PerformanceData 表暂无采集器）"""
        stmt = select(ModelReport).where(
            ModelReport.created_at >= start_dt,
            ModelReport.created_at <= end_dt,
        )
        result = await self.db.execute(stmt)
        reports = result.scalars().all()

        if not reports:
            return {"avg_throughput": None, "avg_p50_latency": None, "avg_p99_latency": None}

        throughputs = []
        p50_latencies = []
        p99_latencies = []

        for r in reports:
            try:
                metrics = json.loads(r.metrics_json) if isinstance(r.metrics_json, str) else r.metrics_json
                if not metrics or not isinstance(metrics, dict):
                    continue
                for key in ["throughput", "avg_throughput", "overall_throughput"]:
                    if key in metrics:
                        throughputs.append(float(metrics[key]))
                        break
                for key in ["p50_latency", "avg_p50_latency", "first_token_latency", "avg_first_token_latency", "ttft"]:
                    if key in metrics:
                        p50_latencies.append(float(metrics[key]))
                        break
                for key in ["p99_latency", "avg_p99_latency"]:
                    if key in metrics:
                        p99_latencies.append(float(metrics[key]))
                        break
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        return {
            "avg_throughput": sum(throughputs) / len(throughputs) if throughputs else None,
            "avg_p50_latency": sum(p50_latencies) / len(p50_latencies) if p50_latencies else None,
            "avg_p99_latency": sum(p99_latencies) / len(p99_latencies) if p99_latencies else None,
        }

    async def _collect_resource_data(self, start_dt, end_dt) -> dict:
        """采集资源看板 NPU 利用率概况"""
        try:
            from infrastructure.persistence.read_models.resource_metrics import (
                ResourceMetricsQueryService,
            )
            svc = ResourceMetricsQueryService(self.db)
            data = await svc.query_npu_metrics(
                time_range="24h", start_time=start_dt, end_time=end_dt
            )
            clusters = []
            for c in data.get("clusters", []):
                metrics = c.get("metrics", [])
                if not metrics:
                    continue
                avg_util = sum(m.get("npu_utilization", 0) for m in metrics) / len(metrics)
                last = metrics[-1]
                clusters.append({
                    "cluster_name": c.get("cluster_name", ""),
                    "avg_npu_utilization": round(avg_util, 1),
                    "npu_total": last.get("npu_total", 0),
                    "npu_used": last.get("npu_used", 0),
                    "executing_pods": last.get("executing_pods_count", 0),
                })
            return {"clusters": clusters}
        except Exception as e:
            logger.warning(f"Failed to collect resource data: {e}")
            return {"clusters": []}

    async def _collect_test_data(self, start_dt, end_dt) -> dict:
        """采集测试看板概况"""
        try:
            from infrastructure.persistence.models.test_board import TestRun

            rows = list((await self.db.execute(
                select(TestRun).where(
                    TestRun.started_at >= start_dt,
                    TestRun.started_at <= end_dt,
                )
            )).scalars().all())
            executed_rows = [
                row for row in rows
                if (row.result or "").lower() in {"passed", "pass", "success", "failed", "fail", "failure", "error"}
            ]
            passed = sum(1 for row in executed_rows if (row.result or "").lower() in {"passed", "pass", "success"})
            flaky_cases = len({row.test_case_id for row in executed_rows if row.flip_detected})
            return {
                "health_score": {},
                "total_cases": len(executed_rows),
                "skipped_cases": len(rows) - len(executed_rows),
                "pass_rate_7d": round(passed / len(executed_rows), 3) if executed_rows else 0,
                "flaky_case_count": flaky_cases,
            }
        except Exception as e:
            logger.warning(f"Failed to collect test data: {e}")
            return {}

    async def _collect_pr_pipeline_data(self, start_dt, end_dt) -> dict:
        """采集 PR 流水线概况"""
        try:
            from infrastructure.persistence.models import PullRequest
            from pr_pipeline.pr_pipeline_service import PRPipelineService
            svc = PRPipelineService()
            overview = await svc.get_overview(self.db, settings.GITHUB_OWNER, settings.GITHUB_REPO, days=1)
            base_conditions = (
                PullRequest.owner == settings.GITHUB_OWNER,
                PullRequest.repo == settings.GITHUB_REPO,
            )
            opened_count = (await self.db.execute(
                select(func.count(PullRequest.id)).where(
                    *base_conditions,
                    PullRequest.created_at >= start_dt,
                    PullRequest.created_at <= end_dt,
                )
            )).scalar() or 0
            merged_count = (await self.db.execute(
                select(func.count(PullRequest.id)).where(
                    *base_conditions,
                    PullRequest.merged_at >= start_dt,
                    PullRequest.merged_at <= end_dt,
                )
            )).scalar() or 0
            return {
                "open_count": overview.open_count,
                "merged_count": overview.merged_count,
                "closed_count": overview.closed_count,
                "backlog_index": overview.backlog_index,
                "backlog_level": overview.backlog_level,
                "merge_rate": overview.merge_rate,
                "avg_time_to_merge_hours": overview.avg_time_to_merge_hours,
                "recent_opened_count": opened_count,
                "recent_merged_count": merged_count,
            }
        except Exception as e:
            logger.warning(f"Failed to collect PR pipeline data: {e}")
            return {}

    async def _collect_diagnosis_stats(self, start_dt, end_dt) -> dict:
        """采集问题定位统计"""
        try:
            from infrastructure.persistence.models import IssueDiagnosisHistory
            yesterday_count = (await self.db.execute(
                select(func.count(IssueDiagnosisHistory.id)).where(
                    IssueDiagnosisHistory.created_at >= start_dt,
                    IssueDiagnosisHistory.created_at <= end_dt,
                )
            )).scalar() or 0
            total_count = (await self.db.execute(
                select(func.count(IssueDiagnosisHistory.id))
            )).scalar() or 0
            liked_count = (await self.db.execute(
                select(func.count(IssueDiagnosisHistory.id)).where(IssueDiagnosisHistory.is_liked)
            )).scalar() or 0
            return {
                "yesterday_count": yesterday_count,
                "total_count": total_count,
                "liked_count": liked_count,
            }
        except Exception as e:
            logger.warning(f"Failed to collect diagnosis stats: {e}")
            return {}

    def build_email_html(self, report_data: dict, chart_cids: list[str] | None = None) -> str:
        """使用 Jinja2 渲染 HTML 邮件（旧模板，降级时使用）"""
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        template = env.get_template("daily_report.html")

        dashboard_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"

        return template.render(
            report_data=report_data,
            report_date=report_data["report_date"],
            dashboard_url=dashboard_url,
            chart_cids=chart_cids or [],
        )

    def build_ai_email_html(self, ai_report_markdown: str, report_date: str,
                            chart_cids: list[str] | None = None) -> str:
        """使用 Jinja2 渲染 LLM 报告 HTML 邮件（新模板）"""
        try:
            import markdown as md_lib
            ai_report_html = md_lib.markdown(ai_report_markdown, extensions=['tables', 'fenced_code'])
        except ModuleNotFoundError:
            from markdown_it import MarkdownIt
            ai_report_html = MarkdownIt("commonmark", {"html": False}).enable("table").render(ai_report_markdown)

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
        template = env.get_template("ai_report_email.html")

        dashboard_url = settings.CORS_ORIGINS[0] if settings.CORS_ORIGINS else "http://localhost:3000"

        return template.render(
            report_date=report_date,
            ai_report_html=ai_report_html,
            dashboard_url=dashboard_url,
            chart_cids=chart_cids or [],
        )

    async def _generate_ai_report(self, report_data: dict) -> str | None:
        """用 LLM 生成洞察式每日报告（替换 300 字截断片段）

        读取 daily-report-writer 技能作为 system prompt，
        将全量看板数据作为 user prompt 传给 LLM。
        """
        try:
            from agent.skill_registry import get_skill_registry
            from infrastructure.clients.llm_client import LLMClient
            from infrastructure.persistence.models.daily_summary import LLMProviderConfig

            # 获取 LLM 配置
            llm_config = (await self.db.execute(
                select(LLMProviderConfig).where(LLMProviderConfig.is_active).limit(1)
            )).scalar_one_or_none()
            if not llm_config:
                logger.warning("No active LLM provider configured, skipping AI report generation")
                return None

            # 获取 system prompt（从 skill_registry 读取 SKILL.md）
            registry = get_skill_registry()
            skill = registry.get_skill_by_scope("daily_report")
            system_prompt = skill.content if skill and skill.content else (
                "你是一名 vLLM Ascend 社区运营分析师。请根据提供的全量看板数据，"
                "生成结构化每日运行报告。报告应包含：一句话总结、整体健康度、"
                "Nightly 流水线概况、PR 流水线概况、项目动态、风险与待办。"
                "每段先结论后数据，空数据板块整段省略。"
            )

            # 构造 user prompt（全量数据 JSON）
            prompt_data = self._build_ai_prompt_data(report_data)
            user_prompt = (
                f"请根据以下全量看板数据生成 {report_data.get('report_date', '昨日')} 的每日运行报告。\n\n"
                f"以下是各时间窗口的聚合数据（JSON 格式）：\n\n"
                f"```json\n{json.dumps(prompt_data, ensure_ascii=False, default=str)}\n```\n\n"
                f"请严格按照系统提示词的板块结构输出 Markdown 格式的报告。"
            )

            client = LLMClient()
            result = await client.generate(
                provider=llm_config.provider,
                model=llm_config.default_model,
                api_key=llm_config.decrypted_api_key,
                api_base=llm_config.api_base_url,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=16384,
            )

            logger.info(f"AI report generated: tokens={result.prompt_tokens + result.completion_tokens}")
            return result.content

        except Exception as e:
            logger.error(f"AI report generation failed: {e}", exc_info=True)
            return None

    @staticmethod
    def _build_ai_prompt_data(report_data: dict) -> dict:
        """Build a bounded, valid LLM payload without truncating serialized JSON."""
        def compact(value, depth=0):
            if depth > 5:
                return None
            if isinstance(value, dict):
                return {key: compact(item, depth + 1) for key, item in value.items()}
            if isinstance(value, list):
                return [compact(item, depth + 1) for item in value[:20]]
            if isinstance(value, str) and len(value) > 800:
                return value[:800] + "…"
            return value

        return compact(report_data)

    async def generate_draft(self, report_date: date) -> DailyReportHistory:
        """Generate and persist a reviewable report without sending email."""
        report_config = await self._get_report_config()
        subject_template = report_config.get("report_subject_template", settings.REPORT_SUBJECT_TEMPLATE)
        history = DailyReportHistory(
            report_date=report_date.isoformat(),
            recipients=report_config.get("report_recipients", ""),
            subject=subject_template.format(date=report_date.isoformat()),
            status="generating",
        )
        self.db.add(history)
        await self.db.flush()
        try:
            report_data = await self.generate_report(report_date)
            history.ci_summary = report_data["yesterday"]["ci"]
            history.model_summary = report_data["yesterday"]["model"]
            history.github_summary = report_data["yesterday"]["github"]
            # Persist the exact aggregate snapshot used by the preview.  This is
            # stored inside the existing JSON column to avoid a schema migration.
            history.performance_summary = {
                **report_data["yesterday"]["performance"],
                "_report_snapshot": report_data,
            }
            history.ai_report_content = await self._generate_ai_report(report_data)
            history.status = "draft"
            if not history.ai_report_content:
                history.error_message = "AI generation unavailable; aggregate data is ready for review"
            await self.db.commit()
            await self.db.refresh(history)
            return history
        except Exception as exc:
            history.status = "failed"
            history.error_message = str(exc)
            await self.db.commit()
            logger.error("Failed to generate daily report draft: %s", exc, exc_info=True)
            return history

    async def send_draft(self, report_id: int) -> DailyReportHistory:
        """Send the reviewed draft without regenerating its AI content."""
        history = (await self.db.execute(
            select(DailyReportHistory).where(DailyReportHistory.id == report_id)
        )).scalar_one_or_none()
        if not history:
            raise ValueError("Report draft not found")
        if history.status != "draft":
            raise ValueError(f"Report is not sendable in status '{history.status}'")
        if not history.ai_report_content:
            raise ValueError("Report draft has no generated content")

        report_config = await self._get_report_config()
        dispatcher = NotificationDispatcher(self.db)
        smtp_config = await dispatcher.get_email_config()
        recipients = [item.strip() for item in report_config.get("report_recipients", "").split(",") if item.strip()]
        cc_recipients = [item.strip() for item in report_config.get("report_cc_recipients", "").split(",") if item.strip()]
        if not recipients:
            raise ValueError("No recipients configured")
        if not smtp_config.get("smtp_host"):
            raise ValueError("SMTP host not configured")

        history.status = "sending"
        await self.db.commit()
        try:
            html_content, chart_images = self.build_draft_email(history)
            result = await dispatcher.send_email(
                subject=history.subject,
                html_content=html_content,
                recipients=recipients,
                cc_recipients=cc_recipients,
                images=chart_images or None,
                config=smtp_config,
                source="daily_report",
                event_type="daily_report",
                severity="info",
                title=history.subject,
                summary=f"每日运行报告 {history.report_date}",
                payload={"report_id": history.id, "report_date": history.report_date},
                dedupe_key=f"daily_report:email:{history.id}",
            )
        except Exception as exc:
            history.status = "failed"
            history.error_message = str(exc)
            await self.db.commit()
            raise
        if result["success"]:
            history.status = "sent"
            history.sent_at = _now_shanghai()
            history.error_message = None
        else:
            history.status = "failed"
            history.error_message = result.get("error", "Unknown email error")
        await self.db.commit()
        await self.db.refresh(history)
        return history

    def build_draft_email(
        self, history: DailyReportHistory, *, inline_images: bool = False
    ) -> tuple[str, dict[str, bytes]]:
        """Render the final draft email; preview and delivery must share this path."""
        if not history.ai_report_content:
            raise ValueError("Report draft has no generated content")
        from tooling.rendering.chart_renderer import render_charts

        report_data = self._get_draft_report_snapshot(history)
        chart_images = render_charts(report_data)
        html = self.build_ai_email_html(
            history.ai_report_content, history.report_date, list(chart_images)
        )
        if inline_images:
            for cid, image in chart_images.items():
                encoded = base64.b64encode(image).decode("ascii")
                html = html.replace(
                    f'src="cid:{cid}"', f'src="data:image/png;base64,{encoded}"'
                )
        return html, chart_images

    async def get_draft_preview_html(self, report_id: int) -> str:
        """Return the browser-safe representation of the exact outgoing email."""
        history = (await self.db.execute(
            select(DailyReportHistory).where(DailyReportHistory.id == report_id)
        )).scalar_one_or_none()
        if not history:
            raise ValueError("Report draft not found")
        html, _ = self.build_draft_email(history, inline_images=True)
        return html

    @staticmethod
    def _get_draft_report_snapshot(history: DailyReportHistory) -> dict:
        """Return the exact draft snapshot, with a fallback for legacy drafts."""
        performance = dict(history.performance_summary or {})
        snapshot = performance.pop("_report_snapshot", None)
        if isinstance(snapshot, dict):
            return snapshot
        return {
            "report_date": history.report_date,
            "timezone": "Asia/Shanghai",
            "yesterday": {
                "ci": history.ci_summary or {},
                "model": history.model_summary or {},
                "github": history.github_summary or {},
                "performance": performance,
            },
            "last_week": {},
            "last_month": {},
        }

    async def send_report(self, report_date: date) -> DailyReportHistory:
        """
        生成报告并发送邮件，记录发送历史

        SMTP 配置从数据库读取，不从 .env 文件读取

        Args:
            report_date: 报告日期（昨日日期）

        Returns:
            DailyReportHistory 记录
        """
        report_config = await self._get_report_config()
        dispatcher = NotificationDispatcher(self.db)
        smtp_config = await dispatcher.get_email_config()

        smtp_host = smtp_config.get("smtp_host", "")
        recipients_str = report_config.get("report_recipients", "")
        cc_str = report_config.get("report_cc_recipients", "")

        recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
        cc_recipients = [r.strip() for r in cc_str.split(",") if r.strip()]

        subject_template = report_config.get("report_subject_template", settings.REPORT_SUBJECT_TEMPLATE)
        subject = subject_template.format(date=report_date.isoformat())

        history = DailyReportHistory(
            report_date=report_date.isoformat(),
            recipients=recipients_str,
            subject=subject,
            status="pending",
        )
        self.db.add(history)
        await self.db.flush()

        try:
            report_data = await self.generate_report(report_date)

            # 生成图表 PNG（CID 内嵌图片）
            from tooling.rendering.chart_renderer import render_charts
            chart_images = render_charts(report_data)
            chart_cids = list(chart_images.keys())
            logger.info(f"Charts rendered: {chart_cids}")

            # 用 LLM 生成洞察式报告
            ai_report = await self._generate_ai_report(report_data)
            if ai_report:
                # LLM 报告直接作为邮件正文（新模板），替代旧模板
                history.ai_report_content = ai_report
                # 同时保留 ai_summary_snippet 供前端兼容
                report_data["yesterday"]["github"]["ai_summary_snippet"] = ai_report
                try:
                    html_content = self.build_ai_email_html(
                        ai_report, report_data["report_date"], chart_cids
                    )
                    logger.info("AI report generated, used as email body with new template")
                except Exception as e:
                    logger.warning(f"Failed to build AI email HTML, falling back to old template: {e}")
                    html_content = self.build_email_html(report_data, chart_cids)
            else:
                # 降级到旧模板
                html_content = self.build_email_html(report_data, chart_cids)
                logger.info("AI report generation skipped or failed, using original template")

            history.ci_summary = report_data["yesterday"]["ci"]
            history.model_summary = report_data["yesterday"]["model"]
            history.github_summary = report_data["yesterday"]["github"]
            history.performance_summary = report_data["yesterday"]["performance"]

            if not recipients:
                history.status = "failed"
                history.error_message = "No recipients configured"
                await self.db.commit()
                return history

            if not smtp_host:
                history.status = "failed"
                history.error_message = "SMTP_HOST not configured"
                await self.db.commit()
                return history

            result = await dispatcher.send_email(
                subject=subject,
                html_content=html_content,
                recipients=recipients,
                cc_recipients=cc_recipients,
                images=chart_images if chart_images else None,
                config=smtp_config,
                source="daily_report",
                event_type="daily_report",
                severity="info",
                title=subject,
                summary=f"每日运行报告 {report_date.isoformat()}",
                payload={"report_id": history.id, "report_date": report_date.isoformat()},
                dedupe_key=f"daily_report:email:{history.id}",
            )

            if result["success"]:
                history.status = "sent"
                history.sent_at = _now_shanghai()
            else:
                history.status = "failed"
                history.error_message = result.get("error", "Unknown error")

            await self.db.commit()
            return history

        except Exception as e:
            history.status = "failed"
            history.error_message = str(e)
            await self.db.commit()
            logger.error(f"Failed to send daily report: {e}", exc_info=True)
            return history

    async def get_report_history(self, limit: int = 20, offset: int = 0) -> tuple[list[DailyReportHistory], int]:
        """查询报告发送历史"""
        count_stmt = select(func.count(DailyReportHistory.id))
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = (
            select(DailyReportHistory)
            .order_by(DailyReportHistory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_latest_report(self) -> DailyReportHistory | None:
        """获取最近一次报告记录"""
        stmt = select(DailyReportHistory).order_by(DailyReportHistory.created_at.desc()).limit(1)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


def _is_model_pass(value) -> bool:
    """Model report status has existed as both string and bool in older paths."""
    return value is True or str(value).lower() == "pass"


def _is_model_fail(value) -> bool:
    return value is False or str(value).lower() == "fail"


def _github_author_login(item: dict) -> str:
    user = item.get("user")
    if isinstance(user, dict):
        return user.get("login", "")
    return item.get("author", "") or item.get("author_name", "")


def _compact_pr(item: dict) -> dict:
    return {
        "number": item.get("number") or item.get("pr_number"),
        "title": item.get("title", ""),
        "state": item.get("state", ""),
        "author": _github_author_login(item),
        "html_url": item.get("html_url", ""),
    }


def _compact_issue(item: dict) -> dict:
    return {
        "number": item.get("number") or item.get("issue_number"),
        "title": item.get("title", ""),
        "state": item.get("state", ""),
        "author": _github_author_login(item),
        "html_url": item.get("html_url", ""),
    }


def _compact_commit(item: dict) -> dict:
    return {
        "short_sha": item.get("short_sha") or str(item.get("sha", ""))[:7],
        "message": item.get("message", ""),
        "author": item.get("author", ""),
        "pr_number": item.get("pr_number"),
        "pr_title": item.get("pr_title"),
        "html_url": item.get("html_url", ""),
    }


def _date_to_utc_start(d: date):
    """将北京时间日期转为 UTC 范围起始"""
    from datetime import datetime, time
    start = datetime.combine(d, time.min, tzinfo=SHANGHAI_TZ)
    return start.astimezone(UTC)


def _date_to_utc_end(d: date):
    """将北京时间日期转为 UTC 范围结束"""
    from datetime import datetime, time
    end = datetime.combine(d, time.max, tzinfo=SHANGHAI_TZ)
    return end.astimezone(UTC)


def _today_shanghai():
    """获取当前上海日期（date 对象），不受服务器系统时区影响"""
    from datetime import datetime
    return datetime.now(SHANGHAI_TZ).date()


def _now_shanghai():
    """获取当前上海时间"""
    from datetime import datetime
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)
