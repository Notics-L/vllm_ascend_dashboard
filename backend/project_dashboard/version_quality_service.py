"""
版本质量评估服务

采集多维度数据（Tag 对比、GitHub Issues/PRs、CI 通过率、模型支持矩阵、仓库统计），
调用 LLM 并使用 version-quality-assessment 技能生成完整的 HTML 质量评估报告。
"""
import html
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.skill_registry import get_skill_registry
from infrastructure.clients.github_cache import get_github_cache
from infrastructure.clients.github_client import GitHubClient
from infrastructure.clients.llm_client import LLMClient, LLMResult
from infrastructure.core.config import settings
from infrastructure.core.github_credentials import global_github_credential
from infrastructure.persistence.models import CIResult, ProjectDashboardConfig
from infrastructure.persistence.models.daily_summary import LLMProviderConfig
from infrastructure.storage.version_quality_file_store import VersionQualityFileStore

logger = logging.getLogger(__name__)

SKILL_SCOPE = "version_quality_assessment"


# ---------------------------------------------------------------------------
# HTML Sanitizer — 基于 stdlib html.parser，移除危险标签和属性（P0-3 安全修复）
# ---------------------------------------------------------------------------

# 需要完全移除的标签（连同内容）
_STRIP_TAGS = {"script", "iframe", "object", "embed", "link", "meta", "base", "form"}
# 需要保留但移除危险属性的标签
# 危险属性前缀（onerror, onclick, onload 等）
_EVENT_ATTR_RE = re.compile(r"^on", re.IGNORECASE)
# 危险 URI scheme
_DANGEROUS_URI_RE = re.compile(
    r"^(javascript|vbscript|data:text/html)", re.IGNORECASE
)

# CDATA 内容元素（内容不转义，否则 CSS 子选择器 body > div 会被破坏）
_CDATA_TAGS = {"style"}


class _SanitizingHTMLParser(HTMLParser):
    """移除 <script>/<iframe> 等标签和 on* 事件属性的 HTML 解析器"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._output: list[str] = []
        self._skip_depth = 0  # 当前处于被移除标签内的深度
        self._cdata_tag: str | None = None  # 当前 CDATA 元素（如 style）

    def _filter_attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        """过滤危险属性，返回安全的属性字符串"""
        safe_attrs: list[str] = []
        for name, value in attrs:
            name_lower = name.lower()
            if _EVENT_ATTR_RE.match(name_lower):
                continue
            if name_lower in ("href", "src") and value:
                # strip 所有空白（含 Tab/换行）防止 java\tscript: bypass
                cleaned = re.sub(r"\s+", "", value)
                if _DANGEROUS_URI_RE.match(cleaned):
                    continue
            safe_attrs.append(
                f' {name}="{html.escape(value or "")}"'
            )
        return "".join(safe_attrs)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        if tag_lower in _STRIP_TAGS:
            self._skip_depth += 1
            return
        if tag_lower in _CDATA_TAGS and self._skip_depth == 0:
            self._cdata_tag = tag_lower
        self._output.append(f"<{tag}{self._filter_attrs(attrs)}>")

    def handle_decl(self, decl: str):
        """保留 <!DOCTYPE html> 声明"""
        if self._skip_depth == 0:
            self._output.append(f"<!{decl}>")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in _STRIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._cdata_tag == tag_lower:
            self._cdata_tag = None
        if self._skip_depth == 0:
            self._output.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        """处理自闭合标签如 <img ... />"""
        tag_lower = tag.lower()
        if tag_lower in _STRIP_TAGS:
            return
        self._output.append(f"<{tag}{self._filter_attrs(attrs)} />")

    def handle_data(self, data: str):
        if self._skip_depth == 0:
            if self._cdata_tag is not None:
                # CDATA 内容（如 CSS）不转义，保持 body > div 等选择器
                self._output.append(data)
            else:
                self._output.append(html.escape(data, quote=False))

    def handle_comment(self, data: str):
        # 跳过注释（可能包含 IE 条件注释）
        pass

    def get_output(self) -> str:
        return "".join(self._output)


def _sanitize_html(html_content: str) -> str:
    """净化 HTML 内容：移除 script/iframe 等标签和 on* 事件属性"""
    if not html_content:
        return ""
    parser = _SanitizingHTMLParser()
    try:
        parser.feed(html_content)
        parser.close()
    except Exception as e:
        logger.warning("HTML sanitization failed, using raw: %s", e)
        return html_content
    return parser.get_output()


class VersionQualityService:
    """版本质量评估报告生成服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.file_store = VersionQualityFileStore()
        self.skill_registry = get_skill_registry()
        self.github_cache = get_github_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_report(
        self,
        base_tag: str,
        head_tag: str,
        force_regenerate: bool = False,
    ) -> dict:
        """
        生成版本质量评估报告

        Args:
            base_tag: 基准 tag
            head_tag: 目标 tag
            force_regenerate: 强制重新生成

        Returns:
            报告元数据字典（含 report_id）
        """
        start = time.monotonic()

        # 1. 检查是否已存在报告（非强制时复用）
        if not force_regenerate:
            existing = await self._find_existing_report(base_tag, head_tag)
            if existing:
                logger.info(
                    "VersionQualityService: reuse existing report %s",
                    existing.get("report_id"),
                )
                return existing

        # 2. 采集数据
        logger.info(
            "VersionQualityService: collecting data for %s..%s",
            base_tag, head_tag,
        )
        data = await self._collect_data(base_tag, head_tag)

        # 3. 构建 prompt
        user_prompt = self._build_user_prompt(base_tag, head_tag, data)
        system_prompt = self._get_system_prompt()

        # 4. 获取 LLM 配置
        llm_config = await self._get_llm_config()

        # 5. 调用 LLM 生成 HTML 报告
        logger.info("VersionQualityService: calling LLM provider=%s model=%s",
                    llm_config.provider, llm_config.default_model)

        llm = LLMClient()
        result: LLMResult = await llm.generate(
            provider=llm_config.provider,
            model=llm_config.default_model,
            api_key=llm_config.decrypted_api_key,
            api_base=llm_config.api_base_url or None,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=16384,
        )

        html_content = self._extract_html(result.content)
        if not html_content:
            # 如果 LLM 未输出有效 HTML，将原始内容包装
            html_content = self._wrap_raw_content(result.content, base_tag, head_tag)

        duration = time.monotonic() - start

        # 6. 保存报告
        metadata = {
            "llm_provider": llm_config.provider,
            "llm_model": llm_config.default_model,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "generation_time_seconds": round(duration, 1),
            "total_commits": data.get("total_commits", 0),
            "open_bugs": data.get("open_bug_count", 0),
            "merged_prs": data.get("merged_pr_count", 0),
            "ci_success_rate": data.get("ci_success_rate"),
            "stars": data.get("repo_stats", {}).get("stars"),
            "forks": data.get("repo_stats", {}).get("forks"),
            "data_sources": list(data.keys()),
        }

        saved = await self.file_store.save_report(
            base_tag=base_tag,
            head_tag=head_tag,
            html_content=html_content,
            metadata=metadata,
        )

        logger.info(
            "VersionQualityService: report generated report_id=%s duration=%.1fs",
            saved["report_id"], duration,
        )
        return saved

    async def list_reports(self) -> list[dict]:
        """列出所有报告"""
        return await self.file_store.list_reports()

    async def get_report_meta(self, report_id: str) -> dict | None:
        return await self.file_store.get_report_meta(report_id)

    async def get_report_html(self, report_id: str) -> str | None:
        return await self.file_store.get_report_html(report_id)

    async def get_html_path(self, report_id: str):
        return await self.file_store.get_html_path(report_id)

    async def delete_report(self, report_id: str) -> bool:
        return await self.file_store.delete_report(report_id)

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    async def _collect_data(self, base_tag: str, head_tag: str) -> dict:
        """并行采集多维数据"""
        data: dict[str, Any] = {
            "base_tag": base_tag,
            "head_tag": head_tag,
        }

        # 1. Tag 对比提交（核心数据）
        try:
            commits = self.github_cache.get_commits_between_tags(base_tag, head_tag)
            data["commits"] = commits or []
            data["total_commits"] = len(commits or [])
            data["commit_summary"] = self._summarize_commits(commits or [])
        except Exception as e:
            logger.warning("VersionQualityService: tag comparison failed: %s", e)
            data["commits"] = []
            data["total_commits"] = 0
            data["commit_summary"] = {}
            data["tag_comparison_error"] = str(e)

        # 2. GitHub API 数据（issues / PRs / repo stats）
        gh_data = await self._collect_github_data()
        data.update(gh_data)

        # 3. CI 通过率
        try:
            data["ci_stats"] = await self._collect_ci_stats()
        except Exception as e:
            logger.warning("VersionQualityService: CI stats failed: %s", e)
            data["ci_stats"] = {}

        # 4. 模型支持矩阵
        try:
            data["model_matrix"] = await self._collect_model_matrix()
        except Exception as e:
            logger.warning("VersionQualityService: model matrix failed: %s", e)
            data["model_matrix"] = {}

        return data

    async def _collect_github_data(self) -> dict:
        """通过 GitHub API 采集 issues/PRs/repo stats"""
        result: dict[str, Any] = {}
        credential = global_github_credential(prefer_environment=True)
        if not credential:
            logger.warning("VersionQualityService: GITHUB_TOKEN not set, skipping GitHub API data")
            return result

        client = GitHubClient(
            credential=credential,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO,
        )
        try:
            # Open bug issues
            try:
                search = await client.search_issues(
                    query=f"repo:{settings.GITHUB_OWNER}/{settings.GITHUB_REPO} is:issue is:open label:bug",
                    per_page=100,
                )
                items = search.get("items", [])
                result["open_bugs"] = [
                    {
                        "number": i.get("number"),
                        "title": i.get("title"),
                        "labels": [lbl.get("name") for lbl in i.get("labels", [])],
                        "created_at": i.get("created_at"),
                        "body_preview": (i.get("body") or "")[:500],
                    }
                    for i in items
                ]
                result["open_bug_count"] = search.get("total_count", len(items))
            except Exception as e:
                logger.warning("VersionQualityService: open bugs fetch failed: %s", e)
                result["open_bugs"] = []
                result["open_bug_count"] = 0

            # Recent merged PRs (last 90 days)
            try:
                since = (datetime.now(UTC) - timedelta(days=90)).strftime("%Y-%m-%d")
                search_pr = await client.search_issues(
                    query=f"repo:{settings.GITHUB_OWNER}/{settings.GITHUB_REPO} is:pr is:merged merged:>={since}",
                    per_page=100,
                    sort="updated",
                    order="desc",
                )
                pr_items = search_pr.get("items", [])
                result["merged_prs"] = [
                    {
                        "number": p.get("number"),
                        "title": p.get("title"),
                        "author": p.get("user", {}).get("login"),
                        "created_at": p.get("created_at"),
                        "merged_at": p.get("pull_request", {}).get("merged_at") or p.get("merged_at"),
                        "labels": [lbl.get("name") for lbl in p.get("labels", [])],
                    }
                    for p in pr_items
                ]
                result["merged_pr_count"] = search_pr.get("total_count", len(pr_items))
            except Exception as e:
                logger.warning("VersionQualityService: merged PRs fetch failed: %s", e)
                result["merged_prs"] = []
                result["merged_pr_count"] = 0

            # Repo stats
            try:
                repo_info = await client.get_repo_info()
                result["repo_stats"] = {
                    "stars": repo_info.get("stargazers_count"),
                    "forks": repo_info.get("forks_count"),
                    "open_issues_count": repo_info.get("open_issues_count"),
                    "subscribers_count": repo_info.get("subscribers_count"),
                    "default_branch": repo_info.get("default_branch"),
                    "updated_at": repo_info.get("updated_at"),
                }
            except Exception as e:
                logger.warning("VersionQualityService: repo stats fetch failed: %s", e)
                result["repo_stats"] = {}
        finally:
            await client.close()

        return result

    async def _collect_ci_stats(self) -> dict:
        """从数据库采集 CI 通过率统计（最近 30 天）"""
        since = datetime.now(UTC) - timedelta(days=30)
        stmt = (
            select(
                CIResult.workflow_name,
                CIResult.conclusion,
                func.count(CIResult.id).label("cnt"),
            )
            .where(CIResult.started_at >= since)
            .group_by(CIResult.workflow_name, CIResult.conclusion)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        workflow_stats: dict[str, dict] = {}
        total_success = 0
        total_runs = 0
        for row in rows:
            wf = row.workflow_name
            conclusion = row.conclusion or "unknown"
            cnt = row.cnt
            workflow_stats.setdefault(wf, {})[conclusion] = cnt
            total_runs += cnt
            if conclusion == "success":
                total_success += cnt

        success_rate = round(total_success / total_runs * 100, 1) if total_runs > 0 else None

        return {
            "workflows": workflow_stats,
            "total_runs_30d": total_runs,
            "total_success_30d": total_success,
            "success_rate_30d": success_rate,
        }

    async def _collect_model_matrix(self) -> dict:
        """从数据库采集模型支持矩阵"""
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == "model_support_matrix"
        )
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()
        if not config:
            return {}

        value = config.config_value or {}
        entries = value.get("entries", [])
        # 精简每条记录，减少 prompt 体积
        summary_entries = [
            {
                "model_name": e.get("model_name"),
                "series": e.get("series"),
                "support": e.get("support"),
                "supported_hardware": e.get("supported_hardware"),
            }
            for e in entries
        ]
        return {
            "total_models": len(summary_entries),
            "models": summary_entries,
            "updated_at": value.get("updated_at") or (config.updated_at.isoformat() if config.updated_at else None),
        }

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _get_system_prompt(self) -> str:
        """使用 version-quality-assessment 技能内容作为 system prompt"""
        skill = self.skill_registry.get_skill_by_scope(SKILL_SCOPE)
        if skill and skill.content:
            return skill.content

        logger.warning("VersionQualityService: skill '%s' not found, using fallback", SKILL_SCOPE)
        return (
            "你是一名专业的开源项目版本质量评估专家。请根据提供的多维度数据，"
            "生成一份完整的 HTML 格式版本质量评估报告。"
            "报告需包含：执行摘要、数据口径、趋势对比、Bug 分析、测试覆盖、社区健康度、"
            "发布评估、加固建议和总结。使用深色主题 HTML。"
        )

    def _build_user_prompt(self, base_tag: str, head_tag: str, data: dict) -> str:
        """构建包含全部采集数据的用户 prompt"""
        lines: list[str] = []
        lines.append(f"# 版本质量评估数据：{base_tag} → {head_tag}")
        lines.append(f"评估日期：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("")

        # Tag 对比
        lines.append("## 一、Tag 对比提交")
        lines.append(f"总提交数：{data.get('total_commits', 0)}")
        summary = data.get("commit_summary", {})
        if summary:
            lines.append("分类统计：")
            for cat, cnt in sorted(summary.items()):
                lines.append(f"- {cat}: {cnt}")
        commits = data.get("commits", [])
        if commits:
            lines.append("\n提交明细（前 50 条）：")
            for c in commits[:50]:
                lines.append(
                    f"- [{c.get('category', 'Misc')}] {c.get('title', '')} "
                    f"(by {c.get('author', '')}, {c.get('date', '')[:10]})"
                )
        if data.get("tag_comparison_error"):
            lines.append(f"\n⚠️ Tag 对比异常：{data['tag_comparison_error']}")
        lines.append("")

        # Open bugs
        lines.append("## 二、Open Bug Issues")
        lines.append(f"Open bug 数量：{data.get('open_bug_count', 0)}")
        bugs = data.get("open_bugs", [])
        if bugs:
            lines.append("Bug 明细（前 30 条）：")
            for b in bugs[:30]:
                lines.append(
                    f"- #{b.get('number')}: {b.get('title', '')} "
                    f"[{', '.join(b.get('labels', []))}] ({b.get('created_at', '')[:10]})"
                )
        lines.append("")

        # Merged PRs
        lines.append("## 三、近期合并 PR（最近 90 天）")
        lines.append(f"合并 PR 数量：{data.get('merged_pr_count', 0)}")
        prs = data.get("merged_prs", [])
        if prs:
            lines.append("PR 明细（前 30 条）：")
            for p in prs[:30]:
                lines.append(
                    f"- #{p.get('number')}: {p.get('title', '')} "
                    f"(by {p.get('author', '')}, merged {str(p.get('merged_at', ''))[:10]})"
                )
        lines.append("")

        # CI stats
        ci = data.get("ci_stats", {})
        lines.append("## 四、CI 通过率（最近 30 天）")
        if ci:
            lines.append(f"总运行数：{ci.get('total_runs_30d', 0)}")
            lines.append(f"成功数：{ci.get('total_success_30d', 0)}")
            lines.append(f"通过率：{ci.get('success_rate_30d')}%")
            wf_stats = ci.get("workflows", {})
            if wf_stats:
                lines.append("各 Workflow 统计：")
                for wf, stats in wf_stats.items():
                    stats_str = ", ".join(f"{k}={v}" for k, v in stats.items())
                    lines.append(f"- {wf}: {stats_str}")
        else:
            lines.append("无 CI 数据")
        lines.append("")

        # Model matrix
        matrix = data.get("model_matrix", {})
        lines.append("## 五、模型支持矩阵")
        if matrix:
            lines.append(f"模型总数：{matrix.get('total_models', 0)}")
            for m in matrix.get("models", []):
                lines.append(
                    f"- {m.get('model_name')} ({m.get('series')}): "
                    f"支持={m.get('support')}, 硬件={m.get('supported_hardware', '-')}"
                )
        else:
            lines.append("无模型支持矩阵数据")
        lines.append("")

        # Repo stats
        repo = data.get("repo_stats", {})
        lines.append("## 六、仓库统计")
        if repo:
            lines.append(f"Stars: {repo.get('stars')}")
            lines.append(f"Forks: {repo.get('forks')}")
            lines.append(f"Open issues: {repo.get('open_issues_count')}")
            lines.append(f"Subscribers: {repo.get('subscribers_count')}")
        else:
            lines.append("无仓库统计数据")
        lines.append("")

        # 指令
        lines.append("## 任务")
        lines.append(
            "请根据以上数据，严格按照 version-quality-assessment 技能的方法论和报告结构，"
            "生成一份完整的 HTML 版本质量评估报告。要求："
        )
        lines.append("1. 输出完整的 `<!DOCTYPE html>` 文档，使用深色主题")
        lines.append("2. 包含 Executive Summary、数据口径、趋势对比、Bug 分析、测试覆盖、社区健康度、发布评估、加固建议、总结")
        lines.append("3. 使用统计卡片、表格、徽章、callout 提示框等组件")
        lines.append("4. 给出明确的版本判定（可发布 / 需修复 / 不建议发布）和 RC 收敛计划")
        lines.append("5. 数据不足的部分如实标注「数据不足」，不要编造")
        lines.append("6. 必须包含正向进展分析")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarize_commits(self, commits: list[dict]) -> dict[str, int]:
        summary: dict[str, int] = {}
        for c in commits:
            cat = c.get("category", "Misc")
            summary[cat] = summary.get(cat, 0) + 1
        return summary

    def _extract_html(self, content: str) -> str:
        """从 LLM 输出中提取 HTML 内容并净化（P0-3 安全修复）"""
        if not content:
            return ""
        text = content.strip()
        extracted = ""
        # 尝试提取 ```html ... ``` 代码块
        if "```html" in text:
            start = text.find("```html")
            end = text.find("```", start + 7)  # 从代码块开始后查找闭合 ```, 避免多代码块时截断
            if start != -1 and end != -1:
                extracted = text[start + 7:end].strip()
                if extracted.startswith("```"):
                    extracted = extracted[3:].strip()
        else:
            # 如果直接以 <!DOCTYPE 或 <html 开头
            lower = text.lower()
            if lower.startswith("<!doctype") or lower.startswith("<html"):
                extracted = text
            else:
                # 尝试定位 <!DOCTYPE
                idx = lower.find("<!doctype")
                if idx == -1:
                    idx = lower.find("<html")
                if idx != -1:
                    extracted = text[idx:]

        if not extracted:
            return ""

        # 净化 HTML：移除 <script>/<iframe>/on* 事件属性等
        extracted = _sanitize_html(extracted)

        # HTML 完整性检查：如果缺少 </html> 闭合标签，标注截断
        if "</html>" not in extracted.lower():
            truncate_notice = (
                '<div style="color:#f85149;padding:12px;margin:16px 0;'
                'border:1px solid #f85149;border-radius:4px;">'
                "⚠️ 报告内容可能被截断（LLM 输出未完整闭合 HTML）</div>"
            )
            extracted = extracted + truncate_notice + "\n</body>\n</html>"

        return extracted

    def _wrap_raw_content(self, content: str, base_tag: str, head_tag: str) -> str:
        """将非 HTML 内容包装为最小 HTML（base_tag/head_tag 已转义防 XSS）"""
        safe = (content or "报告生成失败").replace("<", "&lt;").replace(">", "&gt;")
        safe_base = html.escape(base_tag, quote=True)
        safe_head = html.escape(head_tag, quote=True)
        return (
            "<!DOCTYPE html>\n<html lang='zh-CN'>\n<head>\n<meta charset='UTF-8'>\n"
            f"<title>{safe_base} → {safe_head} 版本质量评估报告</title>\n"
            "<style>body{font-family:sans-serif;background:#0d1117;color:#c9d1d9;"
            "padding:40px;max-width:1150px;margin:0 auto;white-space:pre-wrap;}"
            "</style>\n</head>\n<body>\n"
            f"<h1>{safe_base} → {safe_head} 版本质量评估报告</h1>\n"
            f"<div>{safe}</div>\n</body>\n</html>"
        )

    async def _find_existing_report(self, base_tag: str, head_tag: str) -> dict | None:
        """查找已存在的报告（按 tag 前缀索引查找，避免遍历全部文件）"""
        return await self.file_store.find_report_by_tags(base_tag, head_tag)

    async def _get_llm_config(self) -> LLMProviderConfig:
        """获取激活的 LLM 提供商配置"""
        stmt = select(LLMProviderConfig).where(LLMProviderConfig.is_active == True).limit(1)  # noqa: E712
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()
        if not config:
            raise ValueError(
                "No active LLM provider configured. "
                "请在系统配置 → LLM 配置中设置一个激活的提供商。"
            )
        if not config.api_key:
            raise ValueError(
                f"API Key not configured for provider: {config.provider}. "
                "请在系统配置 → LLM 配置中配置 API Key。"
            )
        return config
