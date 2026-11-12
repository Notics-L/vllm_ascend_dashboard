"""CI 每日综合报错报告数据聚合服务。

核心目标：让流水线全绿。报告聚焦「失败详情」——哪些用例 / 模型失败了、
失败原因是什么、原始报错线索行、AI 定界类别、处理状态；多个失败全部统计。
若窗口内没有任何失败，则直接标记「全绿」。全量运行 / 耗时等综合信息作为
次级信息弱化展示。

聚合指定时间窗口（默认：昨日 20:00 ~ 当日 09:00，北京时间）内的：
- Workflow 运行（CIResult）
- Job（CIJob，含失败运行的全部 jobs 用于上下文）
- 失败记录物化表（DailyFailureRecord：分支/硬件/模型/用例/分类/处理状态）
- AI 失败分析（JobFailureAnalysis：定界类别/根因摘要/改进措施/报错线索行）
- 用例执行（TestRun + TestCase，用于失败用例级明细）

所有对外字段均为 JSON 可序列化类型，前端展示与 PDF / Excel 导出共用。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    CIJob,
    CIResult,
    DailyFailureRecord,
    JobFailureAnalysis,
    ModelFoMaster,
    TestRun,
)

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# Default window boundaries in Beijing time (start=previous day, end=current day).
DEFAULT_WINDOW_START_HOUR = 20
DEFAULT_WINDOW_END_HOUR = 9

# "Original error clue line" length cap.
ERROR_LINE_MAX_LEN = 500
# Top-N slowest failed jobs.
SLOW_TOP_N = 10
# Daily trend lookback (days before the window end date).
TREND_LOOKBACK_DAYS = 14

# Conclusions that count as "failed".
FAILED_CONCLUSIONS = {"failure", "cancelled", "timed_out", "startup_failure"}
PASSED_RESULTS = {"pass", "passed", "success", "succeeded", "ok"}
SKIPPED_RESULTS = {"skip", "skipped"}
FAILED_RESULTS = {"fail", "failed", "error", "failure"}

# Problem-category label palette (for charts / PDF).
CATEGORY_COLORS = {
    "基础设施": "#dc2626",
    "测试用例": "#f59e0b",
    "开发代码": "#2563eb",
    "其他": "#6b7280",
}


@dataclass(frozen=True)
class ReportRequest:
    """Resolved report request: explicit window in UTC + optional filters.

    branch: 过滤来源分支（DailyFailureRecord.source_branch / CIResult.branch）。
             传 "all" 表示不过滤。
    hardware: 过滤硬件。
    """

    start_utc: datetime
    end_utc: datetime
    hardware: str | None = None
    branch: str | None = None
    generated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Window resolution & formatting
# ---------------------------------------------------------------------------

def _now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _to_utc(dt: datetime) -> datetime:
    """Normalize any datetime to a UTC naive datetime (DB stores UTC naive)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def compute_default_window() -> tuple[datetime, datetime]:
    """Default window: yesterday 20:00 -> today 09:00 (Beijing) as UTC."""
    now_s = _now_shanghai()
    today = now_s.date()
    window_start = datetime.combine(
        today - timedelta(days=1), time(hour=DEFAULT_WINDOW_START_HOUR), tzinfo=SHANGHAI_TZ
    )
    window_end = datetime.combine(today, time(hour=DEFAULT_WINDOW_END_HOUR), tzinfo=SHANGHAI_TZ)
    return _to_utc(window_start), _to_utc(window_end)


def parse_window(start: str | None, end: str | None) -> tuple[datetime, datetime]:
    """Parse explicit start/end (Beijing wall clock) into UTC datetimes.

    Accepts "YYYY-MM-DD HH:MM[:SS]" or "YYYY-MM-DD". If only one boundary is
    given, the other is derived using the default 20:00->09:00 (13h) window.
    """
    if not start and not end:
        return compute_default_window()

    span_hours = (24 - DEFAULT_WINDOW_START_HOUR) + DEFAULT_WINDOW_END_HOUR  # 13

    def _parse(value: str, *, is_start: bool) -> datetime:
        value = value.strip()
        fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
        parsed = None
        for fmt in fmts:
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"无法解析时间 {value!r}，请使用 'YYYY-MM-DD HH:MM'")
        if "%H" not in fmt:
            parsed = parsed.replace(hour=DEFAULT_WINDOW_START_HOUR if is_start else DEFAULT_WINDOW_END_HOUR)
        return parsed.replace(tzinfo=SHANGHAI_TZ)

    if start and end:
        return _to_utc(_parse(start, is_start=True)), _to_utc(_parse(end, is_start=False))
    if start:
        s = _parse(start, is_start=True)
        return _to_utc(s), _to_utc(s + timedelta(hours=span_hours))
    e = _parse(end, is_start=False)
    return _to_utc(e - timedelta(hours=span_hours)), _to_utc(e)


def format_beijing(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def format_beijing_date(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d")


def format_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    secs = total % 60
    if minutes < 60:
        return f"{minutes}m{secs}s" if secs else f"{minutes}m"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h{minutes}m"


# 结论值 → 中文显示（前端 / PDF / Excel 共用）
CONCLUSION_ZH = {
    "success": "成功",
    "failure": "失败",
    "cancelled": "已取消",
    "skipped": "已跳过",
    "timed_out": "超时",
    "startup_failure": "启动失败",
}


def format_conclusion_zh(conclusion: str | None) -> str:
    return CONCLUSION_ZH.get((conclusion or "").lower().strip(), conclusion or "未知")


def _classify_conclusion(conclusion: str | None) -> str:
    c = (conclusion or "").lower()
    if c in FAILED_CONCLUSIONS:
        return "failed"
    if c == "success":
        return "passed"
    if c == "skipped":
        return "skipped"
    if c in ("", "none"):
        return "unknown"
    return "other"


def _normalize_category(category: str | None) -> str:
    """Normalize the AI category label (strip whitespace, fallback)."""
    c = (category or "").strip()
    return c if c else "未定界"


# ---------------------------------------------------------------------------
# Error-line extraction (original error clue line from job log)
# ---------------------------------------------------------------------------

def _extract_error_line(log_text: str | None) -> str | None:
    """Return one compact, actionable error line from a GitHub Actions log.

    Reuses the scoring heuristics of the CI API's own log summarizer so the
    problem line matches what the user sees on the job detail page.
    """
    if not log_text:
        return None
    ansi_escape = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    candidates: list[tuple[int, str]] = []
    for raw_line in log_text.splitlines():
        line = ansi_escape.sub("", raw_line).strip()
        lowered = line.lower()
        if not line or any(noise in lowered for noise in (
            'echo "::error::', "::error::upload failed", "process completed with exit code",
        )):
            continue
        # Skip source-code definition lines (not actual runtime log output)
        if re.match(r"^(\s*(def|class)\s|\w+\s*=\s*f?[\"']|\s*(import|from)\s)", line):
            continue
        score = 0
        if any(marker in lowered for marker in (
            "the following aisbench case failed",
            "performance verification failed",
            "accuracy verification failed",
        )):
            score = 10
        elif "traceback" in lowered:
            score = 5
        elif "assertionerror" in lowered or "exception" in lowered:
            score = 4
        elif "error:" in lowered or "[error]" in lowered:
            score = 3
        elif "failed:" in lowered:
            score = 2
        if score:
            candidates.append((score, line))
    if not candidates:
        return None
    best_score = max(item[0] for item in candidates)
    summary = [line for score, line in candidates if score == best_score][-1]
    if len(summary) > ERROR_LINE_MAX_LEN:
        summary = f"{summary[:ERROR_LINE_MAX_LEN - 3]}..."
    return summary


# Keyword-based auto-classification heuristics (fallback when problem_category is missing)
_INFRA_KEYWORDS = (
    "out of memory", "oomkilled", "segmentation fault", "core dumped",
    "fatal error", "fatal python error", "no space left", "disk quota",
    "connection reset", "connection refused", "timeout", "timed out",
    "npu driver", "driver error", "cann error", "runtime error: cuda",
    "hardware error", "device not found", "npu not available",
)
_TEST_KEYWORDS = (
    "aisbench case failed", "test case failed", "test failed",
    "accuracy verification failed", "performance verification failed",
    "result mismatch", "expected", "assertion", "tolerance",
    "case result", "validation failed", "check failed",
)
_CODE_KEYWORDS = (
    "assertionerror", "typeerror", "valueerror", "keyerror",
    "indexerror", "attributeerror", "importerror", "modulenotfounderror",
    "nameerror", "syntaxerror", "runtimeerror", "zerodivisionerror",
    "fileNotFoundError", "permissionerror", "traceback (most recent call last)",
)


def _infer_problem_category(error_text: str | None, failed_step: str | None) -> str | None:
    """Infer a rough problem_category from error text/step name when missing.

    Returns one of "基础设施"/"测试用例"/"开发代码" or None if no confidence.
    """
    text = ((error_text or "") + " " + (failed_step or "")).lower()
    if not text.strip():
        return None
    # Infrastructure: hardware / OOM / driver / timeout
    if any(kw in text for kw in _INFRA_KEYWORDS):
        return "基础设施"
    # Test case: benchmark / verification / assertion against expected
    if any(kw in text for kw in _TEST_KEYWORDS):
        return "测试用例"
    # Code: Python exceptions / tracebacks
    if any(kw in text for kw in _CODE_KEYWORDS):
        return "开发代码"
    return None


def _format_problem_line(category: str | None, error_line: str | None) -> str:
    """Format the problem description as '[初步定界问题范畴][原始报错信息线索行]'."""
    scope = _normalize_category(category)
    line = (error_line or "").strip() or "未提取到原始报错行（点击 GitHub 链接查看完整日志）"
    return f"[{scope}][{line}]"


def _extract_first_failed_step(steps_data: str | None) -> str | None:
    """从 CIJob.steps_data（JSON）中解析首个处于失败态的 step 名称（兼容旧逻辑）。"""
    steps = _extract_all_failed_steps(steps_data)
    return steps[0] if steps else None


def _extract_all_failed_steps(steps_data: str | None) -> list[str]:
    """从 CIJob.steps_data 中解析所有处于失败态的 step 名称列表。

    steps_data 可能是 list（GitHub Actions steps 序列化）或 dict with "steps" key。
    失败态判定：conclusion in {failure, cancelled, timed_out, startup_failure}
    或 status == "failure"。
    """
    if not steps_data:
        return []
    try:
        data = json.loads(steps_data)
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        steps = data.get("steps") or data.get("jobs") or []
    elif isinstance(data, list):
        steps = data
    else:
        return []
    failed_conclusions = {"failure", "cancelled", "timed_out", "startup_failure"}
    out: list[str] = []
    for st in steps:
        if not isinstance(st, dict):
            continue
        conclusion = (st.get("conclusion") or "").lower()
        status = (st.get("status") or "").lower()
        if conclusion in failed_conclusions or status == "failure":
            name = st.get("name") or st.get("title") or st.get("id")
            if name:
                out.append(str(name)[:100])
    return out


def _parse_model_and_yaml(test_model: str | None) -> tuple[str | None, str | None]:
    """从 test_model 字段（如 'Qwen3.8-Flash-Next-w8a8-mxfp8-ntp-A5.yaml'）
    拆分为 (model_name, test_yaml)。

    - 如果包含 '.yaml' / '.yml' 后缀：model_name = 去后缀部分，test_yaml = 完整值
    - 如果无后缀：model_name = 完整值，test_yaml = None
    """
    if not test_model:
        return (None, None)
    name = test_model.strip()
    if not name:
        return (None, None)
    lower = name.lower()
    if lower.endswith(".yaml"):
        model = name[:-5]
        return (model, name)
    if lower.endswith(".yml"):
        model = name[:-4]
        return (model, name)
    return (name, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    f = int(k)
    c = f + 1
    if c >= len(ordered):
        return ordered[f]
    return ordered[f] + (k - f) * (ordered[c] - ordered[f])


def _count_by(items: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        label = str(item) if item not in (None, "") else "未分类"
        out[label] = out.get(label, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


# ---------------------------------------------------------------------------
# Aggregate service
# ---------------------------------------------------------------------------

class CIDailyReportBuilder:
    """Build the structural data for the CI daily comprehensive report."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_report(self, request: ReportRequest) -> dict[str, Any]:
        start, end = request.start_utc, request.end_utc

        runs = await self._query_runs(request)
        failed_run_ids = [r.run_id for r in runs if _classify_conclusion(r.conclusion) == "failed"]
        jobs = await self._query_jobs(start, end, failed_run_ids)
        failure_records = await self._query_failure_records(request)
        case_runs = await self._query_case_runs(start, end)
        analyses = await self._query_analyses({j.job_id for j in jobs if j.job_id})

        # Case-level detail (test run -> test case) for the "which case failed" view.
        case_detail_by_run_job = await self._case_detail_map(case_runs)

        runs_data = [self._run_to_dict(r) for r in runs]
        failures_data = await self._build_failures(
            failure_records, runs, jobs, analyses, case_detail_by_run_job, request
        )
        summary = self._build_summary(runs_data, failures_data, case_runs)

        return {
            "all_green": not failures_data,
            "window": {
                "start_beijing": format_beijing(start),
                "end_beijing": format_beijing(end),
                "start_date": format_beijing_date(start),
                "end_date": format_beijing_date(end),
                "hardware": request.hardware,
                "branch": request.branch,
                "generated_at": format_beijing(request.generated_at or _now_shanghai()),
            },
            "summary": summary,
            "runs": runs_data,
            "failures": failures_data,
            "slowest_failed_jobs": [
                f for f in failures_data if f["duration_seconds"] is not None
            ][-SLOW_TOP_N:][::-1],
            "case_stats": self._case_stats(case_runs),
            "trends": await self._build_trends(start, end, request),
        }

    # ---- queries ----------------------------------------------------------

    async def _query_runs(self, request: ReportRequest) -> list[CIResult]:
        start, end = request.start_utc, request.end_utc
        stmt = select(CIResult).where(CIResult.started_at >= start, CIResult.started_at < end)
        if request.hardware:
            stmt = stmt.where(func.lower(CIResult.hardware) == request.hardware.lower())
        if request.branch and request.branch != "all":
            stmt = stmt.where(CIResult.branch.like(f"{request.branch}%"))
        return list((await self.db.execute(stmt)).scalars().all())

    async def _query_jobs(self, start, end, failed_run_ids) -> list[CIJob]:
        # 时间窗口内的 job + 窗口内失败 run 的全部 job（跨窗口兜底）
        time_cond = and_(CIJob.started_at >= start, CIJob.started_at < end)
        run_cond = CIJob.run_id.in_(failed_run_ids) if failed_run_ids else false()
        stmt = (
            select(CIJob)
            .where(or_(time_cond, run_cond))
            .order_by(CIJob.started_at.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def _query_failure_records(self, request: ReportRequest) -> list[DailyFailureRecord]:
        start_utc, end_utc = request.start_utc, request.end_utc
        # 宽泛日期范围（覆盖跨午夜窗口），再用时间戳二次过滤
        start_date = start_utc.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ).date() - timedelta(days=1)
        end_date = end_utc.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ).date() + timedelta(days=1)
        stmt = select(DailyFailureRecord).where(
            DailyFailureRecord.report_date >= start_date,
            DailyFailureRecord.report_date <= end_date,
        )
        # 分支过滤在 SQL 层做（source_branch 直接存储在 record 上）
        if request.branch and request.branch != "all":
            stmt = stmt.where(DailyFailureRecord.source_branch.like(f"{request.branch}%"))
        # 硬件过滤不在 SQL 层做：rec.hardware 可能为 NULL（fallback 到 CIResult.hardware），
        # 需在 Python 层利用 run_by_id 做 fallback 后过滤。
        records = list((await self.db.execute(stmt)).scalars().all())

        # 批量查 CIResult.hardware 作为 fallback
        run_ids = {rec.run_id for rec in records if rec.run_id}
        run_hw: dict[int, str | None] = {}
        if run_ids:
            run_hw = dict(
                (await self.db.execute(
                    select(CIResult.run_id, CIResult.hardware).where(CIResult.run_id.in_(list(run_ids)))
                )).all()
            )

        def _effective_hw(rec) -> str | None:
            if rec.hardware:
                return rec.hardware.lower()
            return run_hw.get(rec.run_id)

        def _in_window(rec) -> bool:
            ts = rec.started_at or rec.completed_at
            if ts is None:
                return True
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ts_utc_naive = ts.astimezone(UTC).replace(tzinfo=None)
            return start_utc <= ts_utc_naive < end_utc

        result = []
        for rec in records:
            if not _in_window(rec):
                continue
            if request.hardware:
                hw = _effective_hw(rec)
                if hw != request.hardware.lower():
                    continue
            result.append(rec)
        return result

    async def _query_case_runs(self, start, end) -> list[TestRun]:
        stmt = select(TestRun).where(
            TestRun.started_at >= start,
            TestRun.started_at < end,
            TestRun.workflow_name.isnot(None),
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def _query_analyses(self, job_ids: set[int]) -> dict[int, JobFailureAnalysis]:
        if not job_ids:
            return {}
        stmt = select(JobFailureAnalysis).where(JobFailureAnalysis.job_id.in_(list(job_ids)))
        result = await self.db.execute(stmt)
        return {a.job_id: a for a in result.scalars().all() if a.job_id}

    async def _query_job_owners(self, pairs: set[tuple[str, str]]) -> list[tuple[str, str, str, str | None]]:
        """批量查 JobOwner，按 (workflow_name, job_name) 取责任人。
        返回 [(workflow_name, job_name, owner, email), ...]"""
        if not pairs:
            return []
        from infrastructure.persistence.models import JobOwner

        wf_names = {wf for wf, _ in pairs}
        jn_names = {jn for _, jn in pairs}
        stmt = select(
            JobOwner.workflow_name, JobOwner.job_name, JobOwner.owner, JobOwner.email
        ).where(
            JobOwner.workflow_name.in_(list(wf_names)),
            JobOwner.job_name.in_(list(jn_names)),
            JobOwner.is_hidden.is_(False),
        )
        result = await self.db.execute(stmt)
        return [r for r in result.all() if (r[0], r[1]) in pairs]

    async def _query_model_fo_master(
        self,
        test_yamls: set[str],
        model_names: set[str],
    ) -> dict[str, str]:
        """批量查 ModelFoMaster（模型-FO 映射主库）。

        返回 {key: model_fo}，key 同时包含 `yaml:{test_yaml}` 与
        `model:{model_name}` 两种索引，便于 fallback 时按优先级取值。
        仅取 enabled 记录。
        """
        test_yamls = {t for t in test_yamls if t}
        model_names = {m for m in model_names if m}
        if not test_yamls and not model_names:
            return {}
        stmt = select(ModelFoMaster.test_yaml, ModelFoMaster.model_name, ModelFoMaster.model_fo).where(
            ModelFoMaster.enabled.is_(True),
            or_(
                ModelFoMaster.test_yaml.in_(list(test_yamls)) if test_yamls else false(),
                func.lower(ModelFoMaster.model_name).in_([m.lower() for m in model_names]) if model_names else false(),
            ),
        )
        rows = (await self.db.execute(stmt)).all()
        mapping: dict[str, str] = {}
        for test_yaml, model_name, model_fo in rows:
            if test_yaml and model_fo:
                mapping.setdefault(f"yaml:{test_yaml}", model_fo)
            if model_name and model_fo:
                mapping.setdefault(f"model:{model_name.lower()}", model_fo)
        return mapping

    @staticmethod
    def _lookup_model_fo(
        master_map: dict[str, str],
        test_yaml: str | None,
        model_name: str | None,
    ) -> str | None:
        """按 test_yaml 精确 → model_name 小写 顺序查 ModelFoMaster。

        master_map 的 key 形如 `yaml:{test_yaml}` / `model:{model_name 小写}`，
        命中判断必须带同一前缀。
        """
        yaml_key = f"yaml:{test_yaml}" if test_yaml else ""
        if test_yaml and yaml_key in master_map:
            return master_map[yaml_key]
        if model_name:
            return master_map.get(f"model:{model_name.lower()}")
        return None

    async def _case_detail_map(self, case_runs) -> dict[tuple[int, int], dict[str, Any]]:
        """Map (ci_run_id, ci_job_id) -> {test_name, hardware, model...} for
        case-level failure detail. A failed job may correspond to one or more
        executed test cases; we keep the first matching case per job."""
        if not case_runs:
            return {}
        ids = {c.test_case_id for c in case_runs if c.test_case_id}
        case_map: dict[int, Any] = {}
        if ids:
            from infrastructure.persistence.models.test_board import TestCase

            stmt = select(TestCase).where(TestCase.id.in_(list(ids)))
            for tc in (await self.db.execute(stmt)).scalars().all():
                case_map[tc.id] = tc
        out: dict[tuple[int, int], dict[str, Any]] = {}
        for c in case_runs:
            key = (c.ci_run_id, c.ci_job_id) if c.ci_job_id else (c.ci_run_id, -1)
            if key in out:
                continue
            tc = case_map.get(c.test_case_id)
            out[key] = {
                "test_name": (tc.test_name if tc else c.job_name) or c.job_name,
                "hardware": (tc.hardware if tc and tc.hardware else None),
                "result": c.result,
                "failure_category": c.failure_category,
                "duration_seconds": c.duration_seconds,
            }
        return out

    # ---- dict builders ----------------------------------------------------

    def _run_to_dict(self, run: CIResult) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "run_number": run.run_number,
            "workflow_name": run.workflow_name,
            "branch": run.branch or "main",
            "hardware": run.hardware or "未知",
            "event": run.event or "",
            "conclusion": run.conclusion,
            "result": _classify_conclusion(run.conclusion),
            "started_at": format_beijing(run.started_at),
            "completed_at": format_beijing(run.completed_at),
            "duration_seconds": run.duration_seconds,
            "duration_text": format_duration(run.duration_seconds),
            "github_url": self._github_run_url(run.run_id),
        }

    async def _build_failures(
        self,
        failure_records: list[DailyFailureRecord],
        runs: list[CIResult],
        jobs: list[CIJob],
        analyses: dict[int, JobFailureAnalysis],
        case_detail: dict[tuple[int, int], dict[str, Any]],
        request: ReportRequest,
    ) -> list[dict[str, Any]]:
        run_by_id = {r.run_id: r for r in runs}
        # Index: job identity keys -> CIJob (for duration / hardware).
        job_by_key = {(j.run_id, j.job_id): j for j in jobs if j.job_id}

        # 责任人 JobOwner：(workflow_name, job_name) -> (owner, email)
        # 从 CIJob / DailyFailureRecord 中出现过的 (workflow, job_name) 组合去重后一次性查询。
        owner_pairs = set()
        for rec in failure_records:
            owner_pairs.add((rec.workflow_name, rec.job_name))
        for job in jobs:
            owner_pairs.add((job.workflow_name, job.job_name))
        owner_map: dict[tuple[str, str], str] = {}
        if owner_pairs:
            owners = await self._query_job_owners(owner_pairs)
            for (wf, jn, owner, _email) in owners:
                owner_map[(wf, jn)] = owner

        # 模型-FO 主库：收集所有 failure 的 test_yaml / model_name，批量查一次
        master_test_yamls: set[str] = set()
        master_model_names: set[str] = set()
        for rec in failure_records:
            mn, ty = _parse_model_and_yaml(rec.test_model)
            if mn:
                master_model_names.add(mn)
            if ty:
                master_test_yamls.add(ty)
        for job in jobs:
            m_job, t_job = _parse_model_and_yaml(job.test_model if hasattr(job, "test_model") else None)
            if m_job:
                master_model_names.add(m_job)
            if t_job:
                master_test_yamls.add(t_job)
        # case detail 里的 test_name 也可能携带 yaml
        for _key, case in case_detail.items():
            mn_c, ty_c = _parse_model_and_yaml(case.get("test_name"))
            if mn_c:
                master_model_names.add(mn_c)
            if ty_c:
                master_test_yamls.add(ty_c)
        master_map: dict[str, str] = {}
        if master_test_yamls or master_model_names:
            master_map = await self._query_model_fo_master(master_test_yamls, master_model_names)

        def _resolve_owner(
            wf: str,
            jn: str,
            model_fo_direct: str | None,
            test_model: str | None,
        ) -> str | None:
            """owner / 模型FO fallback 链：
            1) DailyFailureRecord.model_fo（人工维护）
            2) JobOwner.owner（workflow+job 维度）
            3) ModelFoMaster 按 test_yaml / model_name 匹配
            """
            if model_fo_direct:
                return model_fo_direct
            owner = owner_map.get((wf, jn))
            if owner:
                return owner
            mn, ty = _parse_model_and_yaml(test_model)
            fo = self._lookup_model_fo(master_map, ty, mn)
            return fo or owner

        items: list[dict[str, Any]] = []
        seen_run_job: set[tuple[int, int | None]] = set()
        seen_run: set[int] = set()

        # 1) Materialized daily-failure records (preferred: they carry branch /
        #    model / case / processing status).
        for rec in failure_records:
            run = run_by_id.get(rec.run_id)
            job = job_by_key.get((rec.run_id, rec.job_id)) if rec.job_id else None
            analysis = analyses.get(rec.job_id) if rec.job_id else None
            case = case_detail.get((rec.run_id, rec.job_id)) or case_detail.get((rec.run_id, -1))

            branch = rec.source_branch or (run.branch if run else None) or "main"
            # hardware: rec.hardware 为空时 fallback 到 CIResult.hardware（workflow 级），再 fallback 到 CIJob
            hw = rec.hardware or (run.hardware if run else None) or (job.hardware if job else None) or "未知"
            hardware = hw
            duration = rec.duration_seconds if rec.duration_seconds is not None else (
                job.duration_seconds if job else None
            )
            error_line = self._error_line_for(analysis, rec, case)
            category = rec.problem_category or (analysis.problem_category if analysis else None)
            # 责任人：DailyFailureRecord.owner（人工维护）优先，缺失时回退 JobOwner
            owner = rec.owner or owner_map.get((rec.workflow_name, rec.job_name))
            # 失败关键步骤：从 CIJob.steps_data 提取首个失败 step
            failed_step = _extract_first_failed_step(job.steps_data if job else None)

            model_name = rec.test_model or (case or {}).get("test_name")
            owner = _resolve_owner(
                rec.workflow_name, rec.job_name, rec.model_fo, model_name,
            )
            items.append(self._failure_item(
                id=rec.id,
                report_date=str(rec.report_date),
                source_branch=branch,
                workflow_name=rec.workflow_name,
                job_name=rec.job_name,
                run_id=rec.run_id,
                job_id=rec.job_id,
                display_name=rec.display_name,
                test_model=model_name,
                model_fo=rec.model_fo,
                deployment_type=rec.deployment_type,
                hardware=hardware,
                owner=owner,
                failed_step=failed_step,
                job=job,
                conclusion=rec.conclusion,
                started_at=rec.started_at,
                completed_at=rec.completed_at,
                duration=duration,
                category=category,
                error_line=error_line,
                analysis=analysis,
                related_pr=rec.related_pr,
                processing_status=rec.processing_status or "未处理",
                notes=rec.notes,
                github_url=rec.github_job_url,
            ))
            if rec.job_id:
                seen_run_job.add((rec.run_id, rec.job_id))
            seen_run.add(rec.run_id)

        # 2) Failed jobs / runs inside the window not covered above (e.g. the
        #    materialized table is not yet populated).
        for job in jobs:
            if _classify_conclusion(job.conclusion) != "failed":
                continue
            if job.job_id and (job.run_id, job.job_id) in seen_run_job:
                continue
            run = run_by_id.get(job.run_id)
            # 硬件过滤：仅保留归属当前硬件过滤范围的 job
            if request.hardware:
                job_hw = (job.hardware or (run.hardware if run else None) or "").lower()
                if job_hw != request.hardware.lower():
                    continue
            analysis = analyses.get(job.job_id) if job.job_id else None
            case = case_detail.get((job.run_id, job.job_id)) or case_detail.get((job.run_id, -1))
            error_line = self._error_line_for(analysis, None, case)
            category = analysis.problem_category if analysis else None
            branch = (run.branch if run else "main") or "main"
            # 责任人：无 DailyFailureRecord，fallback 链 JobOwner → ModelFoMaster
            job_test_model = case.get("test_name") if case else None
            owner = _resolve_owner(job.workflow_name, job.job_name, None, job_test_model)
            # 失败关键步骤：从 CIJob.steps_data 提取首个失败 step
            failed_step = _extract_first_failed_step(job.steps_data)
            # Avoid duplicating an already-covered failed run.
            if not job.job_id and job.run_id in seen_run:
                continue
            items.append(self._failure_item(
                id=None,
                report_date=format_beijing_date(job.started_at),
                source_branch=branch,
                workflow_name=job.workflow_name,
                job_name=job.job_name,
                run_id=job.run_id,
                job_id=job.job_id,
                display_name=None,
                test_model=case.get("test_name") if case else None,
                model_fo=None,
                deployment_type=None,
                hardware=job.hardware or "未知",
                owner=owner,
                failed_step=failed_step,
                job=job,
                conclusion=job.conclusion,
                started_at=job.started_at,
                completed_at=job.completed_at,
                duration=job.duration_seconds,
                category=category,
                error_line=error_line,
                analysis=analysis,
                related_pr=None,
                processing_status="未处理",
                notes=None,
                github_url=self._github_job_url(job.run_id, job.job_id),
            ))
            if job.job_id:
                seen_run_job.add((job.run_id, job.job_id))
            seen_run.add(job.run_id)

        items.sort(key=lambda i: (i["report_date"], i["workflow_name"], i["job_name"]), reverse=True)
        return items

    def _failure_item(self, **kwargs: Any) -> dict[str, Any]:
        category = kwargs.get("category")
        if not category:
            category = _infer_problem_category(
                kwargs.get("error_line") or (kwargs.get("analysis").root_cause_summary if kwargs.get("analysis") else None),
                kwargs.get("failed_step"),
            )
        error_line = kwargs.get("error_line")
        analysis = kwargs.get("analysis")
        started_at: datetime | None = kwargs.get("started_at")
        # 失败日期需精确到秒以便定位（仅有日期无法区分同一天的多次失败）
        if isinstance(started_at, datetime):
            report_date_display = format_beijing(started_at)
        elif started_at and not isinstance(started_at, datetime):
            # DailyFailureRecord.report_date 是纯日期字符串时，回退展示原始值
            report_date_display = f"{started_at} 00:00:00"
        else:
            report_date_display = kwargs.get("report_date") or ""
        # 模型/测试内容拆分
        model_name, test_yaml = _parse_model_and_yaml(kwargs.get("test_model"))
        # 所有失败 steps
        steps_data = None
        job = kwargs.get("job")
        if job is not None and hasattr(job, "steps_data"):
            steps_data = job.steps_data
        all_failed_steps = kwargs.get("failed_steps") or _extract_all_failed_steps(steps_data)
        if not all_failed_steps and kwargs.get("failed_step"):
            all_failed_steps = [kwargs["failed_step"]]
        return {
            "id": kwargs.get("id"),
            "report_date": kwargs.get("report_date"),
            "report_datetime": report_date_display,
            "source_branch": kwargs.get("source_branch") or "main",
            "workflow_name": kwargs.get("workflow_name"),
            "job_name": kwargs.get("job_name"),
            "run_id": kwargs.get("run_id"),
            "job_id": kwargs.get("job_id"),
            "display_name": kwargs.get("display_name"),
            "test_model": kwargs.get("test_model"),
            "model_name": model_name,
            "test_yaml": test_yaml,
            "model_fo": kwargs.get("model_fo"),
            "deployment_type": kwargs.get("deployment_type"),
            "hardware": kwargs.get("hardware") or "未知",
            "owner": kwargs.get("owner"),
            "failed_step": kwargs.get("failed_step"),
            "failed_steps": all_failed_steps,
            "conclusion": kwargs.get("conclusion"),
            "conclusion_zh": format_conclusion_zh(kwargs.get("conclusion")),
            "started_at": format_beijing(started_at),
            "completed_at": format_beijing(kwargs.get("completed_at")),
            "duration_seconds": kwargs.get("duration"),
            "duration_text": format_duration(kwargs.get("duration")),
            "problem_category": category,
            "error_line": error_line,
            "problem_line": _format_problem_line(category, error_line),
            "raw_log_lines": kwargs.get("raw_log_lines"),
            "error_brief": kwargs.get("error_brief"),
            # AI 分析状态：有分析记录时透出（供前端判断是否可点「AI分析」查看详情）
            "analysis_status": (
                analysis.analysis_status if analysis else kwargs.get("analysis_status")
            ),
            # AI 分析详情 Modal 需要 analysisId（JobFailureAnalysis.id）
            "analysis_id": analysis.id if analysis else None,
            "analysis_phase": analysis.analysis_phase if analysis else None,
            "analysis_share_token": analysis.share_token if analysis else None,
            "root_cause_summary": analysis.root_cause_summary if analysis else None,
            "improvement_measures": analysis.improvement_measures_summary if analysis else None,
            "related_pr": kwargs.get("related_pr"),
            "processing_status": kwargs.get("processing_status") or "未处理",
            "notes": kwargs.get("notes"),
            "github_url": kwargs.get("github_url") or self._github_job_url(
                kwargs.get("run_id"), kwargs.get("job_id")
            ),
        }

    @staticmethod
    def _error_line_for(analysis, rec, case) -> str | None:
        """Best-effort original error clue line.

        Priority: AI root-cause summary > TestRun.failure_message > record notes.
        (The full GitHub log is not stored locally; the GitHub link is provided
        so the user can open the raw log.)
        """
        if analysis and analysis.root_cause_summary:
            return analysis.root_cause_summary
        if case and case.get("test_name"):
            # case-level failure_category is a hint, but we need the message;
            # if the record has notes use them, otherwise fall back to category.
            pass
        if rec is not None and rec.notes:
            return rec.notes
        if case and case.get("failure_category"):
            return f"失败类别: {case['failure_category']}"
        if analysis and analysis.improvement_measures_summary:
            return analysis.improvement_measures_summary
        return None

    # ---- 自动触发 AI 失败分析（复用 FailureAnalysisService + TaskManager）----

    async def auto_analyze_missing(self, report: dict[str, Any]) -> dict[str, Any]:
        """对报告中「尚无 AI 分析」的失败项（仅 conclusion=failure，跳过 cancelled/timed_out/
        startup_failure）自动入队触发 AI 失败分析。

        完全复用现有失败分析基础设施：与手动 ``POST /failure-analysis/analyze/{job_id}``
        相同的占位记录 + TaskManager 入队流程，由 Collector worker 在后台抓取 GitHub 日志、
        调用 LLM、落库报告。让「每日综合报错报告」无需逐条手动触发分析。

        返回 { triggered, skipped, total_failures, errors }；单条入队失败不中断整体。
        """
        from sqlalchemy import and_ as _and

        from failure_analysis.failure_analysis import FailureAnalysisService
        from infrastructure.persistence.models import JobFailureAnalysis as _JFA
        from infrastructure.tasks.task_manager import TaskManager

        failures = report.get("failures", [])
        # 仅 conclusion=failure 且尚无 AI 分析记录（analysis_id 为空）的失败项
        targets = [
            f for f in failures
            if (f.get("conclusion") or "").lower() == "failure"
            and f.get("job_id")
            and not f.get("analysis_id")
        ]
        if not targets:
            return {"triggered": 0, "skipped": 0, "total_failures": len(failures), "errors": []}

        service = FailureAnalysisService()
        triggered = 0
        errors: list[str] = []
        for f in targets:
            job_id = f["job_id"]
            try:
                # 读取 CIJob 以复用 compute_failure_fingerprint / workflow_name
                job = (await self.db.execute(
                    select(CIJob).where(CIJob.job_id == job_id)
                )).scalar_one_or_none()
                if job is None:
                    errors.append(f"{job_id}: CIJob 不存在")
                    continue
                conclusion_lower = (job.conclusion or "").lower()
                if conclusion_lower not in ("failure", "timed_out", "startup_failure", "cancelled"):
                    errors.append(f"{job_id}: conclusion 非失败态")
                    continue

                # 检查是否已在分析中：跳过避免重复入队
                existing = (await self.db.execute(
                    select(_JFA).where(_JFA.job_id == job_id)
                    .order_by(_JFA.id.desc()).limit(1)
                )).scalar_one_or_none()
                if existing and existing.analysis_status == "analyzing":
                    continue
                if existing and existing.analysis_status in ("completed", "reused"):
                    continue  # 已有 AI 分析结果，跳过

                # 创建或复用占位记录
                fingerprint = service.compute_failure_fingerprint(job)
                if existing:
                    placeholder = existing
                    placeholder.analysis_status = "analyzing"
                    placeholder.analysis_phase = "queued"
                    placeholder.error_message = None
                    placeholder.failure_fingerprint = fingerprint
                    placeholder.agent_trace = []
                    placeholder.agent_steps = 0
                    placeholder.evidence_ledger = None
                    placeholder.validation_result = None
                    placeholder.reused_analysis_id = None
                    placeholder.report_file_path = None
                    placeholder.pdf_file_path = None
                    placeholder.problem_category = None
                    placeholder.root_cause_summary = None
                    placeholder.improvement_measures_summary = None
                else:
                    placeholder = _JFA(
                        job_id=job_id,
                        run_id=job.run_id,
                        workflow_name=job.workflow_name,
                        job_name=job.job_name,
                        failure_date=job.completed_at,
                        failure_fingerprint=fingerprint,
                        analysis_status="analyzing",
                        analysis_phase="queued",
                    )
                    self.db.add(placeholder)
                await self.db.commit()

                # 入队 Collector worker 异步执行
                await TaskManager.create_task(
                    self.db,
                    "failure_analysis",
                    {"job_id": job_id, "force": False, "triggered_by": "comprehensive_report"},
                    f"failure_analysis:{job_id}",
                    required_capability="python",
                    priority=20,
                )
                await self.db.commit()
                triggered += 1
            except Exception as e:
                errors.append(f"{job_id}: {str(e)[:120]}")
                logger.warning("auto_analyze enqueue failed for job %s: %s", job_id, e)
        return {
            "triggered": triggered,
            "skipped": len(targets) - triggered,
            "total_failures": len(failures),
            "errors": errors[:10],
        }

    # ---- 自动抓取 GitHub 原始日志并解析报错线索 --------------------------

    RAW_LOG_CONCURRENCY = 4
    RAW_LOG_LINES_PER_FAILURE = 5

    @staticmethod
    def _extract_error_lines(log_text: str | None, max_lines: int) -> list[str]:
        """从完整 GitHub Actions 日志中抽取若干高价值的错误线索行。

        策略：
        1. 定位最后一个 traceback / fatal / fatal error 位置作为「失败锚点」
        2. 锚点之后 ± 窗口内的行按关键词打分
        3. 锚点之前的高分行（AISBench case failed / 验证失败）也纳入
        4. 取得分最高的 max_lines 条，同分取最靠近锚点的
        """
        if not log_text:
            return []
        ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
        lines = log_text.splitlines()
        total = len(lines)
        if total == 0:
            return []

        # 找最后一个 traceback 位置（失败锚点）
        anchor = 0
        for i in range(total - 1, -1, -1):
            if "traceback" in lines[i].lower():
                anchor = i
                break

        # anchor 附近的窗口（取锚点前 5 行 ~ 末尾，最多 80 行）
        win_start = max(0, anchor - 5)
        win_end = min(total, anchor + 80) if anchor else total

        scored: list[tuple[int, int, str]] = []  # (score, line_idx, text)
        seen: set[str] = set()
        window = lines[win_start:win_end]
        for i, raw in enumerate(window):
            line = ansi.sub("", raw).strip()
            if not line:
                continue
            lowered = line.lower()
            if any(
                noise in lowered for noise in (
                    'echo "::error::', "::error::upload failed",
                    "process completed with exit code",
                    "##[warning]", "##[notice]",
                )
            ):
                continue
            # Skip source-code definition lines (not actual runtime log output)
            if re.match(r"^(\s*(def|class)\s|\w+\s*=\s*f?[\"']|\s*(import|from)\s)", line):
                continue
            score = 0
            if "the following aisbench case failed" in lowered:
                score = 12
            elif "performance verification failed" in lowered or "accuracy verification failed" in lowered:
                score = 11
            elif "fatal python error" in lowered or "fatal error" in lowered:
                score = 11
            elif "segmentation fault" in lowered or "core dumped" in lowered:
                score = 10
            elif "out of memory" in lowered or "oomkilled" in lowered:
                score = 10
            elif "runtimeerror" in lowered or "runtime error" in lowered:
                score = 9
            elif "assertionerror" in lowered or "assertion failed" in lowered:
                score = 8
            elif "exception" in lowered and "no exception" not in lowered:
                score = 7
            elif "traceback" in lowered:
                score = 6
            elif "error:" in lowered or "[error]" in lowered or "err!" in lowered:
                score = 5
            elif "failed" in lowered and "0 failed" not in lowered and "no failed" not in lowered:
                score = 3
            elif "canceled" in lowered or "cancelled" in lowered:
                score = 2
            if score and line not in seen:
                seen.add(line)
                idx = win_start + i
                proximity = abs(idx - anchor) if anchor else 0
                short = line if len(line) <= ERROR_LINE_MAX_LEN else f"{line[:ERROR_LINE_MAX_LEN - 3]}..."
                scored.append((score, (proximity, idx), short))

        if not scored:
            return []
        # 排序：得分降序 → 接近锚点 → 行号降序（越靠后越接近失败点）
        scored.sort(key=lambda x: (-x[0], x[1][0], -x[1][1]))
        return [item[2] for item in scored[:max_lines]]

    @staticmethod
    def _extract_job_timestamps(log_text: str) -> tuple[str | None, str | None, int | None]:
        """从 GitHub Actions 日志中提取 job 的开始/结束时间和耗时。

        返回 (started_beijing, completed_beijing, duration_seconds)。
        解析 "Job started" / "Job completed" 等标记行中的 UTC 时间戳，
        转为北京时间。若无法解析则返回 (None, None, None)。
        """
        ts_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)"
        )
        start_ts: datetime | None = None
        end_ts: datetime | None = None
        for raw_line in log_text.splitlines():
            lowered = raw_line.lower()
            ts_match = ts_pattern.search(raw_line)
            if not ts_match:
                continue
            raw_ts = ts_match.group(1)
            try:
                if "T" in raw_ts:
                    parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                else:
                    fmts = ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")
                    parsed = None
                    for fmt in fmts:
                        try:
                            parsed = datetime.strptime(raw_ts, fmt)
                            break
                        except ValueError:
                            continue
                    if parsed is None:
                        continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                if start_ts is None and ("job started" in lowered or "job started at" in lowered or "started at" in lowered):
                    start_ts = parsed
                if end_ts is None and ("job completed" in lowered or "completed at" in lowered or "job finished" in lowered):
                    end_ts = parsed
            except (ValueError, OverflowError):
                continue
            if start_ts and end_ts:
                break
        if not start_ts or not end_ts:
            return (None, None, None)
        duration = int((end_ts - start_ts).total_seconds())
        return (
            start_ts.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            end_ts.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            max(duration, 0),
        )

    async def collect_raw_logs(self, report: dict[str, Any]) -> dict[str, Any]:
        """对报告中的失败记录自动访问 GitHub 原始日志链接，拉取并解析报错线索。

        - 对具备 job_id 且 (error_line 缺失 / 未定界 / AI 分析失败) 的失败项拉取。
        - AI 分析失败（analysis_status='failed'）时自动降级：从 GitHub 日志提取完整
          报错 + 精炼一句话（【根因】：【原始线索行】）。
        - 并发受 RAW_LOG_CONCURRENCY 限制，单条抓取网络失败不中断整体。
        - 就地更新 report["failures"]，回写 error_line / raw_log_lines / error_brief。
        - 返回增强后的 report（同一对象）。
        """
        import asyncio

        failures: list[dict[str, Any]] = report.get("failures", [])
        targets = [
            f for f in failures
            if f.get("job_id")
            and (
                not (f.get("error_line") or "").strip()
                or (f.get("problem_category") in (None, "", "未定界"))
                or f.get("analysis_status") == "failed"
            )
        ]
        if not settings.GITHUB_TOKEN:
            logger.warning("GITHUB_TOKEN not configured; skip raw log collection")
            report["_raw_log_stats"] = {
                "targets": len(targets),
                "collected": 0,
                "skipped_no_token": True,
            }
            return report
        if not targets:
            report["_raw_log_stats"] = {"targets": 0, "collected": 0, "skipped_no_token": False}
            return report

        from infrastructure.clients.github_client import GitHubClient

        github = GitHubClient(
            settings.GITHUB_TOKEN,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO,
        )
        sem = asyncio.Semaphore(self.RAW_LOG_CONCURRENCY)
        collected = 0

        async def _one(f: dict[str, Any]) -> None:
            nonlocal collected
            job_id = f.get("job_id")
            if not job_id:
                return
            async with sem:
                try:
                    log_text = await github.get_job_logs(int(job_id))
                except Exception:
                    logger.warning("fetch raw logs failed for job %s", job_id, exc_info=True)
                    return
            if not log_text:
                return
            # 提取时间戳（即使没有错误行也回写时间）
            if not f.get("started_at") or not f.get("started_at").strip():
                ts_start, ts_end, ts_dur = self._extract_job_timestamps(log_text)
                if ts_start:
                    f["started_at"] = ts_start
                    f["report_datetime"] = ts_start
                if ts_end:
                    f["completed_at"] = ts_end
                if ts_dur is not None and not f.get("duration_seconds"):
                    f["duration_seconds"] = ts_dur
                    f["duration_text"] = format_duration(ts_dur)
            lines = self._extract_error_lines(log_text, self.RAW_LOG_LINES_PER_FAILURE)
            if not lines:
                return
            f["raw_log_lines"] = lines
            # 把抓到的首行提升为 error_line（若之前为空），并更新合成 problem_line
            if not (f.get("error_line") or "").strip():
                f["error_line"] = lines[0]
            f["problem_line"] = _format_problem_line(
                f.get("problem_category"), f.get("error_line")
            )
            # 精炼一句话：【根因】：【原始线索行】（AI 失败降级 / 无分析时提供）
            category = (f.get("problem_category") or "未定界").strip()
            clue = (f.get("error_line") or lines[0])[:120]
            f["error_brief"] = f"【{category}】：{clue}"
            # AI 分析失败时标记为降级（前端/报告用 raw_log 替代 AI 结果）
            if f.get("analysis_status") == "failed":
                f["analysis_status"] = "degraded"
            elif not f.get("analysis_status"):
                f["analysis_status"] = "raw_log_collected"
            collected += 1

        await asyncio.gather(*(_one(f) for f in targets))
        report["_raw_log_stats"] = {
            "targets": len(targets),
            "collected": collected,
            "skipped_no_token": not bool(settings.GITHUB_TOKEN),
        }
        logger.info(
            "collected raw logs: %d/%d failures", collected, len(targets)
        )
        return report

    # ---- stats ------------------------------------------------------------

    def _build_summary(self, runs_data, failures_data, case_runs) -> dict[str, Any]:
        total_runs = len(runs_data)
        by_result = _count_by([r["result"] for r in runs_data])
        passed = by_result.get("passed", 0)
        failed = by_result.get("failed", 0)
        skipped = by_result.get("skipped", 0)
        other = max(total_runs - passed - failed - skipped, 0)
        success_rate = round(passed / total_runs * 100, 1) if total_runs else 0.0

        durations = [r["duration_seconds"] for r in runs_data if r["duration_seconds"] is not None]
        durations_sorted = sorted(durations)

        # Failure-side distribution (primary focus).
        failure_by_category = _count_by([_normalize_category(f["problem_category"]) for f in failures_data])
        failure_by_branch = _count_by([f["source_branch"] for f in failures_data])
        failure_by_hardware = _count_by([f["hardware"] for f in failures_data])
        failure_by_model: dict[str, int] = {}
        for f in failures_data:
            model = f["test_model"] or f["display_name"] or f["job_name"] or "未知"
            failure_by_model[str(model)] = failure_by_model.get(str(model), 0) + 1
        failure_by_model = dict(sorted(failure_by_model.items(), key=lambda kv: kv[1], reverse=True))

        processing_distribution = _count_by([f["processing_status"] for f in failures_data])
        unprocessed = processing_distribution.get("未处理", 0)
        analysis_distribution = _count_by([f.get("analysis_status") or "未分析" for f in failures_data])
        top_category = next(iter(failure_by_category), None)

        return {
            "total_runs": total_runs,
            "passed_runs": passed,
            "failed_runs": failed,
            "skipped_runs": skipped,
            "other_runs": other,
            "success_rate": success_rate,
            # Failure focus (primary).
            "total_failed": len(failures_data),
            "failure_by_category": failure_by_category,
            "failure_by_branch": failure_by_branch,
            "failure_by_hardware": failure_by_hardware,
            "failure_by_model": failure_by_model,
            "processing_distribution": processing_distribution,
            "analysis_distribution": analysis_distribution,
            "unprocessed_failures": unprocessed,
            "top_failure_category": top_category,
            "top_failure_category_count": failure_by_category.get(top_category, 0) if top_category else 0,
            # Run-level context (secondary).
            "avg_duration_seconds": round(_mean(durations_sorted), 0) if durations_sorted else None,
            "p90_duration_seconds": _percentile(durations_sorted, 90),
            "max_duration_seconds": durations_sorted[-1] if durations_sorted else None,
            "case_total": len(case_runs),
            "case_pass_rate": self._case_stats(case_runs)["pass_rate"],
        }

    @staticmethod
    def _case_stats(case_runs) -> dict[str, Any]:
        if not case_runs:
            return {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "pass_rate": 0.0}
        passed = sum(1 for c in case_runs if (c.result or "").lower() in PASSED_RESULTS | SKIPPED_RESULTS)
        failed = sum(1 for c in case_runs if (c.result or "").lower() in FAILED_RESULTS)
        skipped = sum(1 for c in case_runs if (c.result or "").lower() in SKIPPED_RESULTS)
        exec_total = len(case_runs) - skipped
        passed_exec = sum(1 for c in case_runs if (c.result or "").lower() in PASSED_RESULTS)
        return {
            "total": len(case_runs),
            "passed": passed_exec,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": round(passed_exec / exec_total * 100, 1) if exec_total else 0.0,
        }

    async def _build_trends(self, start, end, request) -> list[dict[str, Any]]:
        """Daily success-rate trend for N days preceding the window end date."""
        end_date = end.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ).date()
        runs = await self._query_runs(request)
        by_date: dict[str, dict[str, int]] = {}
        for r in runs:
            if not r.started_at:
                continue
            dstr = r.started_at.replace(tzinfo=UTC).astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d")
            bucket = by_date.setdefault(dstr, {"total": 0, "passed": 0, "failed": 0})
            bucket["total"] += 1
            cls = _classify_conclusion(r.conclusion)
            if cls == "passed":
                bucket["passed"] += 1
            elif cls == "failed":
                bucket["failed"] += 1
        trends: list[dict[str, Any]] = []
        for i in range(TREND_LOOKBACK_DAYS - 1, -1, -1):
            d = end_date - timedelta(days=i)
            dstr = d.strftime("%Y-%m-%d")
            bucket = by_date.get(dstr, {"total": 0, "passed": 0, "failed": 0})
            rate = round(bucket["passed"] / bucket["total"] * 100, 1) if bucket["total"] else 0.0
            trends.append({
                "date": dstr,
                "label": d.strftime("%m/%d"),
                "total": bucket["total"],
                "passed": bucket["passed"],
                "failed": bucket["failed"],
                "success_rate": rate,
            })
        return trends

    # ---- github urls ------------------------------------------------------

    @staticmethod
    def _github_run_url(run_id: int | None) -> str | None:
        if not run_id or not settings.GITHUB_OWNER or not settings.GITHUB_REPO:
            return None
        return f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{run_id}"

    @staticmethod
    def _github_job_url(run_id: int | None, job_id: int | None) -> str | None:
        if not run_id or not job_id or not settings.GITHUB_OWNER or not settings.GITHUB_REPO:
            return None
        return f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{run_id}/job/{job_id}"
