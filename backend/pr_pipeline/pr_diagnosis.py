"""PR 问题诊断服务 — 从 GitHub API 获取 CI 失败详情 + 跨 PR 分析 + agentic LLM 深层次定位"""
import asyncio
import base64
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.llm_client import LLMClient
from infrastructure.core.config import settings
from infrastructure.persistence.models import PullRequest
from infrastructure.persistence.models.daily_summary import LLMProviderConfig

logger = logging.getLogger(__name__)

FALLBACK_SYSTEM_PROMPT = """你是一名资深的 vLLM Ascend 社区代码评审专家和 CI/CD 诊断工程师。
分析 PR 的 CI 失败，给出根因分析报告。运用关键日志证据 → 推导过程 → 调用链 → 根因链路的方法论。
重点关注：失败是否与 PR 自身改动相关、是否存在跨 PR 依赖问题（如 skip 标签影响）、是否为 main 分支预存问题。
"""


class PRDiagnosisService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._github_client = None

    def _get_github_client(self):
        if self._github_client is None:
            from infrastructure.clients.github_client import GitHubClient
            self._github_client = GitHubClient()
        return self._github_client

    async def diagnose(self, pr_number: int) -> dict:
        """诊断指定 PR 的 CI 失败，返回深层次诊断报告"""
        context, system_prompt, pr_title = await self.prepare_context(pr_number)
        llm_config = await self._get_llm_config()

        start_time = datetime.now()
        result_content, model_used = await self._call_direct_llm(
            context, system_prompt, llm_config
        )
        duration = (datetime.now() - start_time).total_seconds()

        return {
            "pr_number": pr_number,
            "pr_title": pr_title,
            "report": result_content,
            "model": model_used or llm_config.default_model,
            "provider": llm_config.provider,
            "duration_seconds": round(duration, 1),
            "tokens": 0,
        }

    async def prepare_context(self, pr_number: int) -> tuple[str, str, str]:
        """收集 PR 诊断上下文，供同步和流式诊断复用。"""
        # 1. 获取 PR 信息（DB 优先，GitHub API 降级）
        pr_info = await self._get_pr_info(pr_number)
        head_sha = pr_info.get("head_sha")
        if not head_sha:
            raise ValueError(f"PR #{pr_number} 无法获取 head_sha")

        # 2. 从 GitHub API 获取 CI check runs
        check_runs = await self._get_check_runs(head_sha)
        failing_runs = [r for r in check_runs if r.get("conclusion") == "failure"]

        # 3. 获取失败 Job 的详细日志
        failing_details = []
        for run in failing_runs[:3]:
            detail = await self._get_failing_job_details(run)
            failing_details.append(detail)

        # 4. 获取最近合入的 PR（跨 PR 分析）
        recent_prs = await self._get_recent_merged_prs()

        # 5. 获取 CI 工作流配置（ready/skip 标签逻辑分析）
        workflow_config = await self._get_workflow_config()

        # 6. 构建上下文
        context = self._build_context(
            pr_info, check_runs, failing_details, recent_prs, workflow_config
        )

        # 7. 获取系统提示词
        system_prompt = self._get_system_prompt()
        return context, system_prompt, pr_info.get("title", "")

    async def _call_agentic_llm(self, prompt: str, system_prompt: str, llm_config) -> tuple[str, str]:
        """使用 Claude Code CLI agentic 模式（支持工具调用）"""
        from infrastructure.clients.claude_code_cli import run_with_fallback

        result = await asyncio.wait_for(
            run_with_fallback(
                prompt=prompt,
                provider_config={
                    "provider": llm_config.provider,
                    "api_key": llm_config.decrypted_api_key,
                    "api_base_url": llm_config.api_base_url,
                    "default_model": llm_config.default_model,
                },
                system_prompt=system_prompt,
                max_turns=10,
                timeout_seconds=60,
            ),
            timeout=90,
        )
        return result.content, result.model_used or llm_config.default_model

    async def _call_direct_llm(self, prompt: str, system_prompt: str, llm_config) -> tuple[str, str]:
        """直接调用 LLM（降级模式）"""
        client = LLMClient()
        llm_result = await asyncio.wait_for(
            client.generate(
                provider=llm_config.provider,
                model=llm_config.default_model,
                api_key=llm_config.decrypted_api_key,
                api_base=llm_config.api_base_url,
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.3,
                max_tokens=4096,
            ),
            timeout=300,
        )
        return llm_result.content, llm_config.default_model

    async def _get_pr_info(self, pr_number: int) -> dict:
        """获取 PR 信息：DB 优先，GitHub API 降级"""
        stmt = select(PullRequest).where(
            PullRequest.pr_number == pr_number,
            PullRequest.owner == settings.GITHUB_OWNER,
            PullRequest.repo == settings.GITHUB_REPO,
        )
        result = await self.db.execute(stmt)
        pr = result.scalar_one_or_none()

        if pr:
            return {
                "pr_number": pr.pr_number,
                "title": pr.title,
                "author": pr.author,
                "state": pr.state,
                "head_sha": pr.head_sha,
                "head_branch": pr.head_branch,
                "base_branch": pr.base_branch,
                "labels": pr.labels or [],
                "additions": pr.additions,
                "deletions": pr.deletions,
                "changed_files": pr.changed_files,
                "html_url": pr.html_url,
                "is_draft": pr.is_draft,
            }

        logger.info(f"PR #{pr_number} not in DB, fetching from GitHub API")
        client = self._get_github_client()
        pr_data = await client.get_pr_detail(settings.GITHUB_OWNER, settings.GITHUB_REPO, pr_number)
        return {
            "pr_number": pr_number,
            "title": pr_data.get("title", ""),
            "author": pr_data.get("user", {}).get("login", ""),
            "state": pr_data.get("state", ""),
            "head_sha": pr_data.get("head", {}).get("sha", ""),
            "head_branch": pr_data.get("head", {}).get("ref", ""),
            "base_branch": pr_data.get("base", {}).get("ref", ""),
            "labels": [label.get("name", "") for label in pr_data.get("labels", [])],
            "additions": pr_data.get("additions", 0),
            "deletions": pr_data.get("deletions", 0),
            "changed_files": pr_data.get("changed_files", 0),
            "html_url": pr_data.get("html_url", ""),
            "is_draft": pr_data.get("draft", False),
        }

    async def _get_check_runs(self, head_sha: str) -> list:
        """从 GitHub API 获取 CI check runs"""
        try:
            client = self._get_github_client()
            runs = await client.get_check_runs_for_sha(
                settings.GITHUB_OWNER, settings.GITHUB_REPO, head_sha
            )
            logger.info(f"Got {len(runs)} check runs for SHA {head_sha[:8]}")
            return runs
        except Exception as e:
            logger.warning(f"Failed to fetch check runs: {e}")
            return []

    async def _get_failing_job_details(self, check_run: dict) -> dict:
        """获取失败 check run 的 Job 详情和日志"""
        import re
        check_name = check_run.get("name", "N/A")
        check_url = check_run.get("html_url", check_run.get("details_url", ""))

        # check runs 没有 run_id 字段，从 details_url 解析
        details_url = check_run.get("details_url", "")
        run_id = None
        job_id_from_url = None
        if details_url:
            m = re.search(r'/runs/(\d+)/job/(\d+)', details_url)
            if m:
                run_id = int(m.group(1))
                job_id_from_url = int(m.group(2))
            else:
                m = re.search(r'/runs/(\d+)', details_url)
                if m:
                    run_id = int(m.group(1))

        if not run_id:
            return {"check_name": check_name, "url": check_url, "failing_jobs": []}

        try:
            client = self._get_github_client()

            # 优先用 URL 中的 job_id 直接获取日志
            job_details = []
            if job_id_from_url:
                try:
                    logs = await client.get_job_logs(job_id_from_url)
                    log_excerpt = self._extract_errors_from_logs(logs)
                    if log_excerpt:
                        job_details.append({
                            "job_name": check_name,
                            "runner_name": "N/A",
                            "failed_steps": [],
                            "log_excerpt": log_excerpt,
                        })
                except Exception as e:
                    logger.warning(f"Failed to fetch logs for job {job_id_from_url}: {e}")

            # 如果直接获取失败，尝试列出所有 jobs
            if not job_details:
                jobs = await client.get_job_list(run_id)
                failing_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
                for job in failing_jobs[:3]:
                    j_id = job.get("id")
                    j_name = job.get("name", "N/A")
                    runner = job.get("runner_name", "N/A")
                    failed_steps = [
                        s.get("name", "N/A")
                        for s in job.get("steps", [])
                        if s.get("conclusion") == "failure"
                    ]
                    log_excerpt = ""
                    if j_id:
                        try:
                            logs = await client.get_job_logs(j_id)
                            log_excerpt = self._extract_errors_from_logs(logs)
                        except Exception:
                            pass
                    job_details.append({
                        "job_name": j_name,
                        "runner_name": runner,
                        "failed_steps": failed_steps,
                        "log_excerpt": log_excerpt,
                    })

            return {
                "check_name": check_name,
                "url": check_url,
                "conclusion": check_run.get("conclusion", ""),
                "failing_jobs": job_details,
            }
        except Exception as e:
            logger.warning(f"Failed to get job details for run {run_id}: {e}")
            return {"check_name": check_name, "url": check_url, "failing_jobs": [], "error": str(e)}

    async def _get_recent_merged_prs(self) -> list:
        """获取最近合入的 PR（用于跨 PR 依赖分析）"""
        try:
            client = self._get_github_client()
            url = f"/repos/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/pulls"
            params = {"state": "closed", "sort": "updated", "direction": "desc", "per_page": 30}
            result = await client._request("GET", url, params=params)

            prs = []
            for pr in result:
                if not pr.get("merged_at"):
                    continue
                prs.append({
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "labels": [label.get("name", "") for label in pr.get("labels", [])],
                    "merged_at": pr.get("merged_at"),
                    "user": pr.get("user", {}).get("login", ""),
                })
            logger.info(f"Got {len(prs)} recently merged PRs")
            return prs[:15]
        except Exception as e:
            logger.warning(f"Failed to fetch recent merged PRs: {e}")
            return []

    async def _get_workflow_config(self) -> str:
        """获取 CI 工作流配置（分析 ready/skip 标签逻辑）"""
        try:
            client = self._get_github_client()
            url = f"/repos/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/contents/.github/workflows"
            result = await client._request("GET", url)

            configs = []
            for item in result[:15]:
                name = item.get("name", "")
                if not name.endswith((".yml", ".yaml")):
                    continue
                try:
                    file_url = f"/repos/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/contents/{item['path']}"
                    file_result = await client._request("GET", file_url)
                    content = base64.b64decode(file_result.get("content", "")).decode(
                        "utf-8", errors="replace"
                    )
                    if any(kw in content.lower() for kw in ["ready", "skip", "selected-test", "run-selected", "label"]):
                        # 只截取包含关键词的片段，避免整个 YAML 过大
                        relevant_lines = []
                        for line in content.split("\n"):
                            if any(kw in line.lower() for kw in ["ready", "skip", "label", "selected-test", "run-selected", "if:", "jobs:", "name:"]):
                                relevant_lines.append(line)
                        snippet = "\n".join(relevant_lines[:80])
                        configs.append(f"### {name}\n```yaml\n{snippet[:3000]}\n```")
                except Exception:
                    continue

            return "\n\n".join(configs[:3])
        except Exception as e:
            logger.warning(f"Failed to fetch workflow config: {e}")
            return ""

    def _extract_errors_from_logs(self, logs: str) -> str:
        """从 CI 日志中提取错误相关的行"""
        if not logs:
            return ""

        lines = logs.split("\n")
        error_patterns = [
            "FAILED", "##[error]", "Traceback", "AttributeError", "AssertionError",
            "TypeError", "ValueError", "KeyError", "exit code", "short test summary",
            "failed,", "FAILED TEST LOGS", "Process completed",
            "Executing the custom container",
        ]
        error_lines = []
        seen = set()

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if any(p in stripped for p in error_patterns):
                start = max(0, i - 2)
                end = min(len(lines), i + 8)
                for j in range(start, end):
                    line_text = lines[j].strip()
                    if line_text and line_text not in seen:
                        seen.add(line_text)
                        error_lines.append(line_text[:500])

        result = "\n".join(error_lines[:80])
        return result[:10000]

    def _build_context(
        self, pr: dict, check_runs: list, failing_details: list,
        recent_prs: list, workflow_config: str
    ) -> str:
        """构建 LLM 上下文 — 包含 PR 信息、CI 结果、失败详情、跨 PR 数据、工作流配置"""
        lines = [
            f"## PR #{pr.get('pr_number')} CI 失败深层次诊断请求",
            "",
            "### PR 基本信息",
            f"- 标题: {pr.get('title', 'N/A')}",
            f"- 作者: {pr.get('author', 'N/A')}",
            f"- 状态: {pr.get('state', 'N/A')}" + (" (Draft)" if pr.get('is_draft') else ""),
            f"- 分支: {pr.get('head_branch', 'N/A')} → {pr.get('base_branch', 'N/A')}",
            f"- 代码变更: +{pr.get('additions', 0)} -{pr.get('deletions', 0)} ({pr.get('changed_files', 0)} 文件)",
            f"- Head SHA: {pr.get('head_sha', 'N/A')[:12]}",
            f"- PR URL: {pr.get('html_url', 'N/A')}",
        ]

        labels = pr.get("labels", [])
        if labels:
            lines.append(f"- 标签: {', '.join(labels)}")
            skip_labels = [label for label in labels if "skip" in label.lower()]
            if skip_labels:
                lines.append(f"  ⚠️ 包含 skip 相关标签: {', '.join(skip_labels)}")
        else:
            lines.append("- 标签: 无")

        # CI check runs 汇总
        total = len(check_runs)
        passed = sum(1 for r in check_runs if r.get("conclusion") == "success")
        failed = sum(1 for r in check_runs if r.get("conclusion") == "failure")
        skipped = sum(1 for r in check_runs if r.get("conclusion") in ("skipped", "neutral"))
        lines.append(f"\n### CI Check 汇总（共 {total} 个，✅ {passed} / ❌ {failed} / ⏭️ {skipped}）")
        for run in check_runs[:15]:
            conclusion = run.get("conclusion", "N/A")
            icon = {"success": "✅", "failure": "❌", "skipped": "⏭️", "neutral": "➖"}.get(conclusion, "❓")
            lines.append(f"- {icon} {run.get('name', 'N/A')}: {conclusion}")

        # 失败详情
        if failing_details:
            lines.append(f"\n### 失败详情（{len(failing_details)} 个失败 Check）")
            for detail in failing_details:
                lines.append(f"\n#### {detail.get('check_name', 'N/A')}")
                if detail.get("url"):
                    lines.append(f"- URL: {detail.get('url')}")
                for job in detail.get("failing_jobs", []):
                    lines.append(f"\n**Job: {job.get('job_name', 'N/A')}**")
                    lines.append(f"- Runner: {job.get('runner_name', 'N/A')}")
                    if job.get("failed_steps"):
                        lines.append(f"- 失败步骤: {', '.join(job['failed_steps'])}")
                    if job.get("log_excerpt"):
                        # 转义日志中的代码围栏，防止 prompt injection
                        safe_excerpt = job["log_excerpt"].replace("```", "ˋˋˋ")
                        lines.append("- 关键日志:")
                        lines.append("```")
                        lines.append(safe_excerpt)
                        lines.append("```")

        # 跨 PR 数据
        if recent_prs:
            lines.append(f"\n### 最近合入的 PR（{len(recent_prs)} 个，用于跨 PR 依赖分析）")
            for rp in recent_prs:
                rp_labels = rp.get("labels", [])
                has_ready = "ready" in rp_labels
                has_skip = any("skip" in label.lower() for label in rp_labels)
                label_str = f" 标签: {', '.join(rp_labels)}" if rp_labels else " 无标签"
                ready_flag = ""
                if not has_ready:
                    ready_flag = " ⚠️ 无 ready 标签（CI 可能跳过测试）"
                if has_skip:
                    ready_flag += " ⚠️ 有 skip 标签"
                lines.append(f"- #{rp['number']} {rp['title'][:60]}{label_str}{ready_flag}")

        # CI 工作流配置
        if workflow_config:
            lines.append("\n### CI 工作流配置（分析 ready/skip 标签逻辑）")
            lines.append(workflow_config)

        # 诊断指令
        lines.append("")
        lines.append("请运用根因分析方法论进行深层次诊断，必须覆盖以下维度：")
        lines.append("")
        lines.append("1. **失败与 PR 的关联性**：失败的测试/步骤是否与当前 PR 的改动相关？PR 改了哪些文件？失败在哪个模块？")
        lines.append("2. **跨 PR 依赖分析**：检查最近合入的 PR，特别是那些没有 ready 标签的 PR。")
        lines.append("   - 没有 ready 标签的 PR 合入时 CI 测试被跳过 → 坏代码可能直通主干")
        lines.append("   - 当前 PR 带 ready 标签 → CI 拉到被污染的主干 → 跑到坏测试失败")
        lines.append("3. **工作流配置分析**：分析 CI 工作流 YAML 中的 ready/skip 标签逻辑。")
        lines.append("   - ready 是 opt-in（默认跳过测试）还是 opt-out（默认跑测试）？")
        lines.append("   - 分支保护是否允许 skipped check 满足要求？")
        lines.append("4. **main 分支预存问题**：失败是否在 main 分支上同样存在？")
        lines.append("5. **具体修复建议**：给出代码级修复建议和流水线门禁改进建议。")
        lines.append("")
        lines.append("注意：你当前处于单次调用模式，无法调用工具。请基于以上提供的信息直接进行分析，不要尝试使用 curl 或其他工具。")
        lines.append("请使用中文输出完整的诊断报告，包括标题、章节标题、分析内容、修复建议等全部使用中文。")

        return "\n".join(lines)

    def _get_system_prompt(self) -> str:
        """获取系统提示词：优先从 skill registry 加载 auto-bug-fixer 技能"""
        try:
            from agent.skill_registry import get_skill_registry
            skill = get_skill_registry().get_skill_by_scope('ci_failure_analysis')
            if skill and skill.content:
                logger.info("Using auto-bug-fixer skill for PR diagnosis")
                return skill.content
        except Exception as e:
            logger.warning(f"Failed to load auto-bug-fixer skill: {e}")
        return FALLBACK_SYSTEM_PROMPT

    async def _get_llm_config(self):
        """获取活跃的 LLM 配置"""
        stmt = select(LLMProviderConfig).where(LLMProviderConfig.is_active).limit(1)
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()
        if not config:
            raise ValueError("No active LLM provider configured")
        if not config.api_key:
            raise ValueError(f"API Key not configured for provider: {config.provider}")
        return config
