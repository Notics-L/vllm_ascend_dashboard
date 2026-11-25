"""上游测试结果采集。

数据来自公共 GitHub 仓库 ``vllm-ascend/upstream-test``，仓库里只有 CSV 文件，
因此不需要 GitHub token（匿名读取 raw 内容即可）。匿名速率限制只影响 API 调用，
raw.githubusercontent.com 不占用 API 配额。

仓库结构::

    weekly/upstream_cases_<YYYYMMDDHHMMSS>.csv   本周用例快照（每次 CI 覆盖新增一份）
    local/<version>.csv                          某个 vLLM 版本的本地上游用例结果
    issue/issue.csv                              未关闭的关联 issue
    issue/issue_resolved.csv                     已关闭的关联 issue
    issue/issue_other.csv                        其他状态（已修复待确认、非问题等）

CSV 列固定为：``test_path,status,关键片段,分析,原因分类,版本,是否已删除``。
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SOURCE_REPO = "vllm-ascend/upstream-test"
SOURCE_BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_BRANCH}"
API_BASE = f"https://api.github.com/repos/{SOURCE_REPO}"

# E2E-upstream workflow（上游用例的 CI 执行入口）
DEFAULT_CI_OWNER = "vllm-project"
DEFAULT_CI_REPO = "vllm-ascend"
DEFAULT_CI_WORKFLOW_FILE = "e2e-upstream.yaml"

# vLLM 主社区仓库：用于统计上游 tests 目录下的用例规模（可迁移率的分母）
UPSTREAM_TEST_OWNER = "vllm-project"
UPSTREAM_TEST_REPO = "vllm"
UPSTREAM_TEST_DEFAULT_REF = "main"

WEEKLY_PREFIX = "upstream_cases_"
VERSION_RE = re.compile(r"^v\d+(?:\.\d+)*$", re.IGNORECASE)
WEEKLY_TS_RE = re.compile(r"(\d{14})")

STATUS_KEYS = ("passed", "failed", "skipped", "unknown")

# 首页失败原因展示的最大字符数：横条下方要能一行扫完，完整分析留在详情页
SUMMARY_CHARS = 40


def _shorten(text: str, limit: int = SUMMARY_CHARS) -> str:
    """截断到 ``limit`` 个字符，超出部分用省略号代替。"""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _norm_status(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in STATUS_KEYS:
        return raw
    if raw in {"pass", "success"}:
        return "passed"
    if raw in {"fail", "failure", "error"}:
        return "failed"
    if raw in {"skip"}:
        return "skipped"
    return "unknown" if raw else "unknown"


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in STATUS_KEYS}
    for row in rows:
        counts[_norm_status(row.get("status"))] = counts.get(_norm_status(row.get("status")), 0) + 1
    total = len(rows)
    counts["total"] = total
    return counts


def parse_case_csv(text: str) -> list[dict[str, Any]]:
    """解析用例 CSV，兼容 BOM 与空行。"""
    if not text:
        return []
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        test_path = (raw.get("test_path") or "").strip()
        if not test_path:
            continue
        rows.append(
            {
                "test_path": test_path,
                "status": _norm_status(raw.get("status")),
                "snippet": (raw.get("关键片段") or "").strip() or None,
                "analysis": (raw.get("分析") or "").strip() or None,
                "category": (raw.get("原因分类") or "").strip() or None,
                "case_version": (raw.get("版本") or "").strip() or None,
                "removed": (raw.get("是否已删除") or "").strip() or None,
            }
        )
    return rows


def parse_issue_csv(text: str, state: str) -> list[dict[str, Any]]:
    """解析 issue CSV。

    ``影响程度`` / ``影响`` 两列目前在上游基本为空，保留原值以便后续填充。
    """
    if not text:
        return []
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        number_raw = (raw.get("issue") or "").strip()
        number: int | None = None
        if number_raw.isdigit():
            number = int(number_raw)
        title = (raw.get("title") or "").strip() or None
        test_path = (raw.get("test_path") or "").strip() or None
        if number is None and not title:
            continue
        rows.append(
            {
                "state": state,
                "issue_number": number,
                "issue_title": title,
                "test_path": test_path,
                "impact_level": (raw.get("影响程度") or "").strip() or None,
                "impact": (raw.get("影响") or "").strip() or None,
                "notes": (raw.get("notes") or "").strip() or None,
            }
        )
    return rows


def _pick_latest_weekly(names: list[str]) -> str | None:
    candidates = [n for n in names if n.startswith(WEEKLY_PREFIX) and n.endswith(".csv")]
    if not candidates:
        return None

    def sort_key(name: str) -> str:
        match = WEEKLY_TS_RE.search(name)
        return match.group(1) if match else ""

    return sorted(candidates, key=sort_key)[-1]


def _pick_latest_version(names: list[str]) -> str | None:
    versions = [n for n in names if n.lower().endswith(".csv") and VERSION_RE.match(n[:-4])]
    if not versions:
        return None

    def sort_key(name: str) -> tuple[int, ...]:
        parts = name[:-4].lstrip("vV").split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)

    return sorted(versions, key=sort_key)[-1]


def _version_sort_key(name: str) -> tuple[int, ...]:
    parts = name[:-4].lstrip("vV").split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def _pick_previous_version(names: list[str], latest: str | None) -> str | None:
    """返回版本号小于 ``latest`` 的最近一个版本文件。

    页面需要「相对上一版本」的口径时才用到；上游只保留有限几个版本文件。
    """
    if not latest:
        return None
    versions = [
        n
        for n in names
        if n.lower().endswith(".csv") and VERSION_RE.match(n[:-4]) and n != latest
    ]
    older = [n for n in versions if _version_sort_key(n) < _version_sort_key(latest)]
    if not older:
        return None
    return sorted(older, key=_version_sort_key)[-1]


def _failure_reasons(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """把「未通过」用例聚合成「原因 + 条数」，供首页直接展示失败原因。

    口径说明：``skipped`` 也要计入。上游把「无法执行」的用例标成 ``skipped``
    而不是 ``failed``（例如「无法执行-后端」58 条全部是 skipped），只看 failed
    会严重低估后端适配缺口。因此只排除真正通过的 ``passed``。

    标签优先用 ``原因分类``（本身是短标签）；缺失时退回 ``分析`` 并截断。
    """
    counts: dict[str, int] = {}
    for row in rows:
        if _norm_status(row.get("status")) == "passed":
            continue
        category = (row.get("category") or "").strip()
        analysis = (row.get("analysis") or "").strip()
        if category:
            label = category
        elif analysis:
            label = _shorten(analysis)
        else:
            label = "未分类"
        counts[label] = counts.get(label, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"reason": reason, "count": count} for reason, count in ordered[:limit]]


class UpstreamTestCollector:
    """从 upstream-test 仓库抓取并汇总上游测试结果。"""

    def __init__(
        self,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        attempts: int = 5,
    ) -> httpx.Response:
        """带重试的 GET。

        raw.githubusercontent.com 访问不稳定（常见 SSL EOF / 连接重置 / 读超时），
        而每次失败都会让某份 CSV 整份缺失，因此这里重试刻意给得比普通请求多。
        """
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError:
                raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                # TransportError 覆盖 ConnectError/ReadError/RemoteProtocolError，
                # 包括 SSL EOF；TimeoutException 覆盖连接/读/写超时。
                last_exc = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(min(2.0 * (attempt + 1), 8.0))
        raise last_exc if last_exc else RuntimeError("unreachable")

    async def _list_dir(self, client: httpx.AsyncClient, path: str) -> list[str]:
        response = await self._get(
            client, f"{API_BASE}/contents/{path}", params={"ref": SOURCE_BRANCH}
        )
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [item.get("name", "") for item in payload if item.get("type") == "file"]

    async def _read_raw(self, client: httpx.AsyncClient, path: str) -> str:
        response = await self._get(client, f"{RAW_BASE}/{path}")
        return response.text

    async def count_upstream_tests(
        self,
        client: httpx.AsyncClient,
        *,
        version: str | None,
        token: str = "",
        owner: str = UPSTREAM_TEST_OWNER,
        repo: str = UPSTREAM_TEST_REPO,
    ) -> dict[str, Any]:
        """统计 vLLM 主仓库 ``tests/`` 下的 ``test_*.py`` 文件数量。

        口径：
        - 仓库：``vllm-project/vllm``（vLLM 主社区仓库）
        - 路径：``tests/`` 目录及其所有子目录
        - 文件名：以 ``test_`` 开头、``.py`` 结尾的文件（Git blob 计数，不做任何去重）
        - 版本：优先用当前分析版本对应的 tag（如 ``v0.33.0``），拿不到则退回默认分支

        用 Git Trees API 一次性递归取全量文件列表（v0.33.0 约 9600 个条目，未截断），
        比逐个目录遍历省请求。该接口消耗 API 额度，失败只降级为"未统计"。
        """
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        refs = [ref for ref in (version, UPSTREAM_TEST_DEFAULT_REF) if ref]
        last_error: str | None = None
        for ref in refs:
            try:
                response = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}",
                    params={"recursive": "1"},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - 总用例数是辅助指标
                last_error = f"{ref}: {exc}"
                logger.warning("Failed to count upstream tests at %s: %s", ref, exc)
                continue

            tree = payload.get("tree") or []
            count = sum(
                1
                for item in tree
                if item.get("type") == "blob"
                and item.get("path", "").startswith("tests/")
                and item["path"].endswith(".py")
                and item["path"].rsplit("/", 1)[-1].startswith("test_")
            )
            return {
                "available": True,
                "repo": f"{owner}/{repo}",
                "ref": ref,
                "count": count,
                "truncated": bool(payload.get("truncated")),
                "rule": "tests/**/test_*.py（Git 树中 blob 文件计数）",
            }

        return {
            "available": False,
            "repo": f"{owner}/{repo}",
            "ref": version,
            "count": None,
            "reason": last_error or "未获取到仓库文件树",
        }

    async def fetch_ci_summary(
        self,
        client: httpx.AsyncClient,
        *,
        token: str = "",
        owner: str = DEFAULT_CI_OWNER,
        repo: str = DEFAULT_CI_REPO,
        workflow_id_override: int | None = None,
    ) -> dict[str, Any]:
        """读取 E2E-upstream 最近若干天的运行情况。

        未配置 token 时 GitHub API 匿名额度有限，失败不影响主体数据，只返回
        ``available=False``。
        """
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # 仓库 workflow 数量远超一页（40000+ runs），全局 runs 列表里几乎
        # 看不到 E2E-upstream。按 workflow id 查询最稳，id 来自仓库
        # .github/workflows/e2e-upstream.yaml，可用环境变量覆盖。
        workflow_id: int | None = workflow_id_override
        if workflow_id is None:
            try:
                listing = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/actions/workflows",
                    params={"per_page": 100},
                    headers=headers,
                )
                listing.raise_for_status()
                for item in (listing.json() or {}).get("workflows") or []:
                    if (item.get("path") or "").lower().endswith(DEFAULT_CI_WORKFLOW_FILE):
                        workflow_id = item.get("id")
                        break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to resolve workflow id: %s", exc)

        if workflow_id is None:
            return {"available": False, "reason": "未找到 E2E-upstream workflow id"}

        try:
            runs_resp = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs",
                params={"per_page": 50},
                headers=headers,
            )
            runs_resp.raise_for_status()
            runs = (runs_resp.json() or {}).get("workflow_runs") or []
        except Exception as exc:  # noqa: BLE001 - CI 数据是附属信息
            logger.warning("Failed to list E2E-upstream runs: %s", exc)
            return {"available": False, "reason": str(exc), "workflow_id": workflow_id}

        workflow_name = "E2E-upstream"
        workflow_path = f".github/workflows/{DEFAULT_CI_WORKFLOW_FILE}"

        # 本周窗口：以本周一 00:00 UTC 为起点。周初窗口内往往还没有定时运行，
        # 此时统计回退到最近 7 天的定时运行，保证"数字"和"列表"口径一致。
        since = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        weekday = since.weekday()
        week_start = since - timedelta(days=weekday)
        rolling_start = since - timedelta(days=7)

        def parse_time(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

        def shape(run: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": run.get("id"),
                "run_number": run.get("run_number"),
                "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "branch": run.get("head_branch"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "html_url": run.get("html_url"),
            }

        all_shaped = [shape(run) for run in runs]
        scheduled_all = [r for r in all_shaped if r.get("event") == "schedule"]
        week_scheduled = [
            r for r in scheduled_all if (parse_time(r.get("created_at")) or week_start) >= week_start
        ]
        rolling_scheduled = [
            r for r in scheduled_all if (parse_time(r.get("created_at")) or rolling_start) >= rolling_start
        ]

        # 统计窗口：优先本周；本周没有定时运行时退到最近 7 天，避免周初页面
        # 显示"0 次运行"而下方又列出上一轮定时运行的矛盾。
        stats_scope = "week"
        stats_start = week_start
        window_runs = week_scheduled
        if not window_runs:
            stats_scope = "last_7_days"
            stats_start = rolling_start
            window_runs = rolling_scheduled
        if not window_runs:
            window_runs = all_shaped

        # 展示哪一次运行：优先本周定时运行，其次最近一次定时运行。
        latest: dict[str, Any] | None = None
        fallback_used = False
        if week_scheduled:
            latest = week_scheduled[0]
        elif scheduled_all:
            latest = scheduled_all[0]
            fallback_used = True
        elif all_shaped:
            latest = all_shaped[0]
            fallback_used = True

        pr_total = sum(
            1
            for r in all_shaped
            if r.get("event") != "schedule"
            and (parse_time(r.get("created_at")) or stats_start) >= stats_start
        )

        succeeded = sum(1 for r in window_runs if r.get("conclusion") == "success")
        failed = sum(1 for r in window_runs if r.get("conclusion") == "failure")
        cancelled = sum(1 for r in window_runs if r.get("conclusion") == "cancelled")

        return {
            "available": True,
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "workflow_path": workflow_path,
            "workflow_url": (
                f"https://github.com/{owner}/{repo}/actions/workflows/{workflow_id}"
            ),
            "repo_url": f"https://github.com/{owner}/{repo}",
            "week_start": week_start.isoformat(),
            "stats_scope": stats_scope,
            "stats_start": stats_start.isoformat(),
            "total": len(window_runs),
            "scheduled_total": len(window_runs),
            "pr_total": pr_total,
            "fallback_used": fallback_used,
            "succeeded": succeeded,
            "failed": failed,
            "cancelled": cancelled,
            "latest": latest,
            "runs": window_runs[:10],
            "recent_scheduled": scheduled_all[:5],
        }

    async def collect(
        self,
        *,
        ci_token: str = "",
        ci_owner: str = DEFAULT_CI_OWNER,
        ci_repo: str = DEFAULT_CI_REPO,
        ci_workflow_id: int | None = None,
    ) -> dict[str, Any]:
        """抓取一次完整结果。

        每个数据源独立降级：某份 CSV 或 CI 接口失败时记录 warning，其余数据照常
        入库，避免一次瞬断导致整天的数据缺失。全部数据源都失败才上抛。
        """
        warnings: list[str] = []

        async def safe(coro, label: str, default: Any) -> Any:
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 - 单源降级
                logger.warning("Upstream test source %s failed: %s", label, exc)
                warnings.append(f"{label}: {exc}")
                return default

        async with await self._client() as client:
            weekly_names = await safe(self._list_dir(client, "weekly"), "weekly 目录", [])
            local_names = await safe(self._list_dir(client, "local"), "local 目录", [])

            weekly_file = _pick_latest_weekly(weekly_names)
            version_file = _pick_latest_version(local_names)
            previous_version_file = _pick_previous_version(local_names, version_file)

            weekly_cases: list[dict[str, Any]] = []
            if weekly_file:
                raw = await safe(
                    self._read_raw(client, f"weekly/{weekly_file}"), weekly_file, None
                )
                if raw is None:
                    # 用例 CSV 抓不到时必须显式告警，否则页面会显示"0 条用例"，
                    # 比报错更容易误导人。
                    warnings.append(f"{weekly_file}: 抓取失败，本周用例数据缺失")
                else:
                    weekly_cases = parse_case_csv(raw)

            version_cases: list[dict[str, Any]] = []
            if version_file:
                raw = await safe(
                    self._read_raw(client, f"local/{version_file}"), version_file, None
                )
                if raw is None:
                    warnings.append(f"{version_file}: 抓取失败，版本用例数据缺失")
                else:
                    version_cases = parse_case_csv(raw)

            issues: list[dict[str, Any]] = []
            for path, state in (
                ("issue/issue.csv", "open"),
                ("issue/issue_resolved.csv", "resolved"),
                ("issue/issue_other.csv", "other"),
            ):
                raw = await safe(self._read_raw(client, path), path, None)
                if raw is None:
                    warnings.append(f"{path}: 抓取失败，{state} issue 数据缺失")
                    continue
                issues.extend(parse_issue_csv(raw, state))

            ci_summary = await safe(
                self.fetch_ci_summary(
                    client,
                    token=ci_token,
                    owner=ci_owner,
                    repo=ci_repo,
                    workflow_id_override=ci_workflow_id,
                ),
                "CI 运行信息",
                {"available": False, "reason": "获取失败"},
            )

            # 必须在 client 关闭前调用：这里要复用同一个 httpx client
            upstream_tests = await safe(
                self.count_upstream_tests(
                    client,
                    version=version_file[:-4] if version_file else None,
                    token=ci_token,
                ),
                "上游总用例数",
                {"available": False, "count": None, "reason": "获取失败"},
            )

        if not weekly_cases and not version_cases and not issues:
            raise RuntimeError(
                "上游测试数据全部获取失败" + (f"：{'; '.join(warnings)}" if warnings else "")
            )

        issue_counts = {"open": 0, "resolved": 0, "other": 0}
        for row in issues:
            if row.get("issue_number"):
                row["issue_url"] = (
                    f"https://github.com/{DEFAULT_CI_OWNER}/{DEFAULT_CI_REPO}"
                    f"/issues/{row['issue_number']}"
                )
            issue_counts[row["state"]] = issue_counts.get(row["state"], 0) + 1

        # 「新增 issue」= 与最新版本用例关联（用例路径相同）的未关闭 issue 数。
        # 这正是"这个版本新增用例冒出来的问题"：issue 表里的 test_path 指向的
        # 用例属于最新版本用例集，才算这一版新增用例带来的 issue。
        version_paths = {row["test_path"] for row in version_cases}
        version_open_issue_numbers = {
            row["issue_number"]
            for row in issues
            if row["state"] == "open"
            and row.get("issue_number")
            and (row.get("test_path") or "") in version_paths
        }
        issue_added_count = len(version_open_issue_numbers)

        # 版本用例里的未通过用例，按原因分类聚合成「简单原因」列表，
        # 供首页横条下方直接展示，避免用户为看原因而先点进详情。
        version_failure_reasons = _failure_reasons(version_cases)
        weekly_failure_reasons = _failure_reasons(weekly_cases)

        return {
            "fetched_at": _utcnow(),
            "source_repo": SOURCE_REPO,
            "weekly_file": weekly_file,
            "weekly_cases": weekly_cases,
            "weekly_summary": _summary(weekly_cases),
            "weekly_failure_reasons": weekly_failure_reasons,
            "version_name": version_file[:-4] if version_file else None,
            "version_file": version_file,
            "previous_version_name": previous_version_file[:-4] if previous_version_file else None,
            "version_cases": version_cases,
            "version_summary": _summary(version_cases),
            "version_failure_reasons": version_failure_reasons,
            "issues": issues,
            "issue_summary": issue_counts,
            "issue_added_count": issue_added_count,
            "upstream_tests": upstream_tests,
            "ci_summary": ci_summary,
            "warnings": warnings,
        }
