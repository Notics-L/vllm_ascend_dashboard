"""
每日总结服务
"""
import asyncio
import logging
import os
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.claude_code_cli import run_with_fallback
from infrastructure.clients.github_cache import get_github_cache_for_repo
from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.persistence.models import ProjectDashboardConfig
from infrastructure.persistence.models.daily_summary import (
    DailyCommit,
    DailyIssue,
    DailyPR,
    LLMProviderConfig,
)
from infrastructure.storage.daily_data_file_store import DailyDataFileStore

logger = logging.getLogger(__name__)


class DailyData:
    """每日数据聚合"""
    def __init__(self, prs: list[dict], issues: list[dict], commits: list[dict]):
        self.prs = prs
        self.issues = issues
        self.commits = commits

    @property
    def has_data(self) -> bool:
        return len(self.prs) > 0 or len(self.issues) > 0 or len(self.commits) > 0


class SummaryResult:
    """总结结果"""
    def __init__(
        self,
        project: str,
        date: date,
        summary_markdown: str,
        has_data: bool,
        pr_count: int,
        issue_count: int,
        commit_count: int,
        llm_provider: str,
        llm_model: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        generation_time_seconds: int | None = None,
        status: str = 'success',
        error_message: str | None = None,
    ):
        self.project = project
        self.date = date
        self.summary_markdown = summary_markdown
        self.has_data = has_data
        self.pr_count = pr_count
        self.issue_count = issue_count
        self.commit_count = commit_count
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.generation_time_seconds = generation_time_seconds
        self.status = status
        self.error_message = error_message


class DailySummaryService:
    """每日总结服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.github_client = GitHubClient()
        self.file_store = DailyDataFileStore()

    async def refresh_pr_issue_status(
        self,
        project: str,
        fetch_date: date
    ) -> tuple[int, int]:
        """
        刷新指定日期的 PR 和 Issue 状态

        Args:
            project: 项目标识
            fetch_date: 日期

        Returns:
            (PR 数量，Issue 数量)
        """
        # 1. 根据项目确定 owner 和 repo
        if project == "vllm":
            owner, repo = "vllm-project", "vllm"
        else:
            owner, repo = settings.GITHUB_OWNER, settings.GITHUB_REPO

        # 2. 获取该日期的所有 PR
        stmt = select(DailyPR).where(
            DailyPR.project == project,
            DailyPR.data_date == fetch_date,
        )
        result = await self.db.execute(stmt)
        prs = result.scalars().all()

        # 3. 获取该日期的所有 Issue
        stmt = select(DailyIssue).where(
            DailyIssue.project == project,
            DailyIssue.data_date == fetch_date,
        )
        result = await self.db.execute(stmt)
        issues = result.scalars().all()

        # 4. 并发获取所有 PR 的最新状态
        pr_tasks = []
        for pr in prs:
            pr_tasks.append(self._refresh_pr_status(owner, repo, pr))

        pr_results = await asyncio.gather(*pr_tasks, return_exceptions=True)
        updated_pr_count = sum(1 for r in pr_results if isinstance(r, bool) and r)

        # 5. 并发获取所有 Issue 的最新状态
        issue_tasks = []
        for issue in issues:
            issue_tasks.append(self._refresh_issue_status(owner, repo, issue))

        issue_results = await asyncio.gather(*issue_tasks, return_exceptions=True)
        updated_issue_count = sum(1 for r in issue_results if isinstance(r, bool) and r)

        await self.db.commit()

        logger.info(f"Refreshed status for {updated_pr_count} PRs and {updated_issue_count} issues on {fetch_date}")
        return (len(prs), len(issues))

    async def _refresh_pr_status(
        self,
        owner: str,
        repo: str,
        pr: DailyPR
    ) -> bool:
        """刷新单个 PR 的状态"""
        try:
            pr_detail = await self.github_client.get_pr_detail(owner, repo, pr.pr_number)
            if not pr_detail:
                return False

            # 更新状态
            pr.state = pr_detail.get('state', pr.state)

            # 更新合入信息
            if pr_detail.get('merged'):
                pr.state = 'merged'
                pr.merged_at = self._parse_datetime(pr_detail.get('merged_at'))

            # 更新其他信息
            pr.title = pr_detail.get('title', pr.title)
            pr.author = pr_detail.get('user', {}).get('login', pr.author)
            pr.labels = pr_detail.get('labels', pr.labels)

            await self.db.flush()
            return True
        except Exception as e:
            logger.error(f"Failed to refresh PR #{pr.pr_number} status: {e}")
            return False

    async def _refresh_issue_status(
        self,
        owner: str,
        repo: str,
        issue: DailyIssue
    ) -> bool:
        """刷新单个 Issue 的状态"""
        try:
            issue_detail = await self.github_client.get_issue(owner, repo, issue.issue_number)
            if not issue_detail:
                return False

            # 更新状态
            issue.state = issue_detail.get('state', issue.state)

            # 更新关闭信息
            if issue.state == 'closed':
                issue.closed_at = self._parse_datetime(issue_detail.get('closed_at'))

            # 更新其他信息
            issue.title = issue_detail.get('title', issue.title)
            issue.author = issue_detail.get('user', {}).get('login', issue.author)
            issue.labels = issue_detail.get('labels', issue.labels)

            await self.db.flush()
            return True
        except Exception as e:
            logger.error(f"Failed to refresh Issue #{issue.issue_number} status: {e}")
            return False

    def _parse_datetime(self, dt_str: str | None) -> datetime | None:
        """
        解析 GitHub API 返回的时间字符串

        Args:
            dt_str: ISO 格式的时间字符串，如 "2026-04-10T12:30:45Z"

        Returns:
            datetime 对象或 None（转换为 UTC 时间存储）
        """
        if not dt_str:
            return None

        try:
            # GitHub API 返回的时间格式：2026-04-10T12:30:45Z
            # 替换 Z 为 +00:00 以解析为带时区的 datetime
            if dt_str.endswith('Z'):
                dt_str = dt_str[:-1] + '+00:00'
            dt = datetime.fromisoformat(dt_str)
            # 转换为 UTC 时间存储（数据库存储 naive datetime，我们统一用 UTC）
            return dt.astimezone(UTC).replace(tzinfo=None)
        except (ValueError, TypeError):
            logger.warning(f"Failed to parse datetime: {dt_str}")
            return None

    async def fetch_daily_data(self, project: str, fetch_date: date, force_refresh: bool = False) -> DailyData:
        """
        获取指定日期的 GitHub 数据

        Args:
            project: 项目标识（ascend/vllm）
            fetch_date: 日期（自然日，按北京时间计算）
            force_refresh: 是否强制刷新（删除已有数据后重新采集）

        Returns:
            包含 PR、Issue、Commit 的数据对象
        """
        # 1. 计算时间范围：北京时间 00:00:00 - 23:59:59
        # 使用 Asia/Shanghai 时区（UTC+8）
        shanghai_tz = ZoneInfo('Asia/Shanghai')

        start_time = datetime.combine(fetch_date, time.min, tzinfo=shanghai_tz)
        end_time = datetime.combine(fetch_date, time.max, tzinfo=shanghai_tz)

        # 转换为 UTC 时间用于 GitHub API 查询
        start_time_utc = start_time.astimezone(UTC)
        end_time_utc = end_time.astimezone(UTC)

        # 2. 根据项目确定 owner 和 repo
        if project == "vllm":
            owner, repo = "vllm-project", "vllm"
        else:  # ascend
            owner, repo = settings.GITHUB_OWNER, settings.GITHUB_REPO

        # 3. 获取 PR 列表
        prs = await self.github_client.get_pull_requests_by_date_range(
            owner, repo, start_time_utc, end_time_utc
        )

        # 4. 获取 Issue 列表
        issues = await self.github_client.get_issues_by_date_range(
            owner, repo, start_time_utc, end_time_utc
        )

        # 5. 获取 Commit 列表
        # 对于所有项目，都尝试从本地 git 缓存获取 commits
        # 如果缓存不存在或获取失败，对于 vLLM 项目则从 PR 获取
        # 注意：传入北京时间，因为 git log 使用本地时区
        commits = await self._get_commits_from_local_cache(
            owner, repo, start_time, end_time
        )

        # 如果本地缓存获取失败且是 vLLM 项目，则从 PR 获取 commit 详情
        if not commits and project == "vllm":
            logger.info(f"Local cache unavailable for {owner}/{repo}, fetching commits from PRs")
            commits = await self._fetch_vllm_commits_from_prs(owner, repo, prs)

        # 6. 保存到文件存储
        # 如果强制刷新，先删除已有文件
        if force_refresh:
            await self.file_store.delete_daily_data(project, fetch_date)
            logger.info(f"Deleted existing data for {project} on {fetch_date} (force refresh)")

        await self.file_store.save_daily_data(
            project=project,
            data_date=fetch_date,
            prs=prs,
            issues=issues,
            commits=commits,
        )

        return DailyData(prs=prs, issues=issues, commits=commits)

    async def _fetch_vllm_commits_from_prs(
        self,
        owner: str,
        repo: str,
        prs: list[dict]
    ) -> list[dict]:
        """
        从 vLLM 项目的 PR 中获取 commit 详情
        """
        commits = []

        # 并发获取所有 PR 的 commits 和详情
        tasks = []
        for pr in prs:
            tasks.append(self._fetch_pr_commits_detail(owner, repo, pr))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch PR commits: {result}")
                continue
            commits.extend(result)

        return commits

    async def _fetch_pr_commits_detail(
        self,
        owner: str,
        repo: str,
        pr: dict
    ) -> list[dict]:
        """获取单个 PR 的所有 commits 详情"""
        try:
            pr_commits = await self.github_client.get_pr_commits(
                owner, repo, pr["number"]
            )

            pr_detail = await self.github_client.get_pr_detail(
                owner, repo, pr["number"]
            )

            commit_details = []
            for commit in pr_commits:
                commit_detail = {
                    "sha": commit["sha"],
                    "short_sha": commit["sha"][:7],
                    "message": commit["commit"]["message"],
                    "full_message": commit["commit"]["message"],
                    "author": commit["commit"]["author"]["name"],
                    "author_email": commit["commit"]["author"].get("email", ""),
                    "committed_at": commit["commit"]["committer"]["date"],
                    "html_url": commit["html_url"],
                    "pr_number": pr["number"],
                    "pr_title": pr["title"],
                    "pr_description": pr_detail.get("body", ""),
                    "files_changed": commit.get("files", []),
                    "additions": commit.get("stats", {}).get("additions", 0),
                    "deletions": commit.get("stats", {}).get("deletions", 0),
                }
                commit_details.append(commit_detail)

            return commit_details
        except Exception as e:
            logger.error(f"Failed to fetch PR commits detail: {e}")
            return []

    async def _get_commits_from_local_cache(
        self,
        owner: str,
        repo: str,
        start_time: datetime,
        end_time: datetime
    ) -> list[dict]:
        """从本地 git 缓存获取 commits"""
        try:
            # 根据 owner 和 repo 获取对应的缓存实例
            cache = get_github_cache_for_repo(owner=owner, repo=repo)
            commits = []

            # 使用 git log 获取指定时间范围的 commits
            git_commits = cache.get_commits_by_date_range(start_time, end_time)

            for commit in git_commits:
                commits.append({
                    "sha": commit["sha"],
                    "short_sha": commit["sha"][:7],
                    "message": commit["message"],
                    "full_message": commit.get("message", ""),
                    "author": commit["author"],
                    "author_email": commit.get("author_email", ""),
                    "committed_at": commit["committed_at"],
                    "html_url": f"https://github.com/{owner}/{repo}/commit/{commit['sha']}",
                })

            return commits
        except Exception as e:
            logger.error(f"Failed to get commits from local cache for {owner}/{repo}: {e}")
            return []

    async def _delete_daily_data(self, project: str, fetch_date: date) -> None:
        """删除指定日期的所有数据（用于强制刷新）"""
        try:
            # 删除 PRs
            stmt = DailyPR.__table__.delete().where(
                DailyPR.project == project,
                DailyPR.data_date == fetch_date,
            )
            await self.db.execute(stmt)

            # 删除 Issues
            stmt = DailyIssue.__table__.delete().where(
                DailyIssue.project == project,
                DailyIssue.data_date == fetch_date,
            )
            await self.db.execute(stmt)

            # 删除 Commits
            stmt = DailyCommit.__table__.delete().where(
                DailyCommit.project == project,
                DailyCommit.data_date == fetch_date,
            )
            await self.db.execute(stmt)

            await self.db.commit()
            logger.info(f"Deleted daily data for {project} on {fetch_date}")
        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to delete daily data: {e}")
            raise

    async def _save_to_database(
        self,
        project: str,
        fetch_date: date,
        prs: list[dict],
        issues: list[dict],
        commits: list[dict]
    ) -> None:
        """保存数据到数据库（使用 upsert：已存在的记录更新，新记录插入）"""
        try:
            # 保存 PRs
            for pr in prs:
                stmt = select(DailyPR).where(
                    DailyPR.project == project,
                    DailyPR.pr_number == pr["number"],
                    DailyPR.data_date == fetch_date,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.title = pr["title"]
                    existing.state = pr["state"]
                    existing.author = pr["user"]["login"]
                    existing.merged_at = self._parse_datetime(pr.get("merged_at"))
                    existing.labels = pr.get("labels", [])
                    existing.body = pr.get("body", "")
                    existing.commits = pr.get("commits", [])
                else:
                    db_pr = DailyPR(
                        project=project,
                        pr_number=pr["number"],
                        title=pr["title"],
                        state=pr["state"],
                        author=pr["user"]["login"],
                        created_at=self._parse_datetime(pr.get("created_at")),
                        merged_at=self._parse_datetime(pr.get("merged_at")),
                        html_url=pr["html_url"],
                        labels=pr.get("labels", []),
                        body=pr.get("body", ""),
                        commits=pr.get("commits", []),
                        data_date=fetch_date,
                    )
                    self.db.add(db_pr)

            # 保存 Issues
            for issue in issues:
                stmt = select(DailyIssue).where(
                    DailyIssue.project == project,
                    DailyIssue.issue_number == issue["number"],
                    DailyIssue.data_date == fetch_date,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.title = issue["title"]
                    existing.state = issue["state"]
                    existing.author = issue["user"]["login"]
                    existing.closed_at = self._parse_datetime(issue.get("closed_at"))
                    existing.labels = issue.get("labels", [])
                    existing.body = issue.get("body", "")
                    existing.comments_count = issue.get("comments", 0)
                else:
                    db_issue = DailyIssue(
                        project=project,
                        issue_number=issue["number"],
                        title=issue["title"],
                        state=issue["state"],
                        author=issue["user"]["login"],
                        created_at=self._parse_datetime(issue.get("created_at")),
                        closed_at=self._parse_datetime(issue.get("closed_at")),
                        html_url=issue["html_url"],
                        labels=issue.get("labels", []),
                        body=issue.get("body", ""),
                        comments_count=issue.get("comments", 0),
                        data_date=fetch_date,
                    )
                    self.db.add(db_issue)

            # 保存 Commits
            for commit in commits:
                # Validate required fields
                if not commit.get("committed_at"):
                    logger.warning(f"Commit missing committed_at, using fetch_date as fallback: {commit.get('sha')}")
                    # Use fetch_date with time 00:00:00 as fallback
                    shanghai_tz = ZoneInfo('Asia/Shanghai')
                    fallback_datetime = datetime.combine(fetch_date, time.min, tzinfo=shanghai_tz)
                    fallback_datetime_utc = fallback_datetime.astimezone(UTC)
                    commit["committed_at"] = fallback_datetime_utc.isoformat().replace('+00:00', 'Z')

                stmt = select(DailyCommit).where(
                    DailyCommit.project == project,
                    DailyCommit.sha == commit["sha"],
                    DailyCommit.data_date == fetch_date,
                )
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.message = commit["message"]
                    existing.full_message = commit.get("full_message", commit["message"])
                    existing.author = commit["author"]
                    existing.author_email = commit.get("author_email", "")
                    existing.committed_at = self._parse_datetime(commit.get("committed_at"))
                    existing.pr_number = commit.get("pr_number")
                    existing.pr_title = commit.get("pr_title")
                    existing.pr_description = commit.get("pr_description")
                    existing.files_changed = commit.get("files_changed", [])
                    existing.additions = commit.get("additions", 0)
                    existing.deletions = commit.get("deletions", 0)
                else:
                    db_commit = DailyCommit(
                        project=project,
                        sha=commit["sha"],
                        short_sha=commit["short_sha"],
                        message=commit["message"],
                        full_message=commit.get("full_message", commit["message"]),
                        author=commit["author"],
                        author_email=commit.get("author_email", ""),
                        committed_at=self._parse_datetime(commit.get("committed_at")),
                        html_url=commit["html_url"],
                        pr_number=commit.get("pr_number"),
                        pr_title=commit.get("pr_title"),
                        pr_description=commit.get("pr_description"),
                        files_changed=commit.get("files_changed", []),
                        additions=commit.get("additions", 0),
                        deletions=commit.get("deletions", 0),
                        data_date=fetch_date,
                    )
                    self.db.add(db_commit)

            await self.db.commit()
            logger.info(f"Saved {len(prs)} PRs, {len(issues)} Issues, {len(commits)} Commits for {project} on {fetch_date}")

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to save daily data: {e}")
            raise

    async def generate_summary(
        self,
        project: str,
        summary_date: date,
        llm_provider: str | None = None,
        force_regenerate: bool = False
    ) -> SummaryResult:
        """
        生成每日总结

        Args:
            project: 项目标识
            summary_date: 日期
            llm_provider: LLM 提供商（可选）
            force_regenerate: 是否强制重新生成

        Returns:
            总结结果
        """
        try:
            # 1. 检查是否已存在总结
            if not force_regenerate:
                existing = await self._get_existing_summary(project, summary_date)
                if existing:
                    return existing

            # 2. 从文件存储获取当日数据
            daily_data = await self._get_daily_data_from_files(project, summary_date)

            # 3. 构建提示词
            prompt = self._build_prompt(project, daily_data, summary_date)

            # 4. 调用 LLM（Agent Service 或 Claude Code CLI）
            llm_config = await self._get_llm_config(llm_provider)
            system_prompt = await self._get_system_prompt(project)

            provider_config = {
                "provider": llm_config.provider,
                "api_key": llm_config.decrypted_api_key,
                "api_base_url": llm_config.api_base_url,
                "default_model": llm_config.default_model,
            }

            if os.environ.get("AGENT_SERVICE_ENABLED", "").lower() in ("1", "true", "yes"):
                from agent.agent_service import AgentService, AgentTask

                agent_svc = AgentService(self.db)
                agent_result = await agent_svc.run(AgentTask(
                    prompt=prompt,
                    provider_config=provider_config,
                    system_prompt=system_prompt,
                    max_steps=8,
                    memory_type="daily_summary",
                    memory_filters={"project": project},
                ))
                if agent_result.exit_code != 0:
                    raise RuntimeError(f"Agent generation failed: {agent_result.error_message}")
                summary_markdown = agent_result.content
                model_used = agent_result.model_used
                generation_time = int(agent_result.duration_seconds)
            else:
                summary_result = await run_with_fallback(
                    prompt=prompt,
                    provider_config=provider_config,
                    system_prompt=system_prompt,
                    max_turns=8,
                )
                summary_markdown = summary_result.content
                model_used = summary_result.model_used or llm_config.default_model
                generation_time = int(summary_result.duration_seconds)

            # 5. 保存总结到数据库
            summary = await self._save_summary(
                project=project,
                summary_date=summary_date,
                summary_markdown=summary_markdown,
                llm_provider=llm_config.provider,
                llm_model=model_used,
                prompt_tokens=None,  # CLI 模式下不可用
                completion_tokens=None,
                generation_time_seconds=generation_time,
                has_data=daily_data.has_data,
                pr_count=len(daily_data.prs),
                issue_count=len(daily_data.issues),
                commit_count=len(daily_data.commits)
            )

            return summary

        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            # 保存错误状态
            await self._save_error_summary(project, summary_date, str(e))
            raise

    async def _get_existing_summary(self, project: str, summary_date: date) -> SummaryResult | None:
        """从文件存储获取已存在的总结"""
        data = await self.file_store.load_summary(project, summary_date)

        if not data or data.get('status') != 'success':
            return None

        return SummaryResult(
            project=data['project'],
            date=date.fromisoformat(data['data_date']),
            summary_markdown=data['summary_markdown'],
            has_data=data.get('has_data', False),
            pr_count=data.get('pr_count', 0),
            issue_count=data.get('issue_count', 0),
            commit_count=data.get('commit_count', 0),
            llm_provider=data.get('llm_provider', ''),
            llm_model=data.get('llm_model', ''),
            prompt_tokens=data.get('prompt_tokens'),
            completion_tokens=data.get('completion_tokens'),
            generation_time_seconds=data.get('generation_time_seconds'),
            status=data.get('status', 'success'),
        )

    async def _get_daily_data_from_files(self, project: str, summary_date: date) -> DailyData:
        """从文件存储获取当日数据"""
        data = await self.file_store.load_daily_data(project, summary_date)

        if not data:
            return DailyData(prs=[], issues=[], commits=[])

        return DailyData(
            prs=data.get("pull_requests", []),
            issues=data.get("issues", []),
            commits=data.get("commits", [])
        )

    def _model_to_dict(self, obj) -> dict:
        """SQLAlchemy 模型转字典"""
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    async def _get_system_prompt(self, project: str) -> str:
        """获取系统提示词"""
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == 'daily_summary_system_prompt'
        )
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()

        if config and config.config_value:
            prompts = config.config_value
            return prompts.get(project, '')

        # 默认提示词
        return f"""你是一名专业的 {project} 项目分析师。请根据以下数据生成项目动态总结和分析。

要求：
1. 总结 PR 趋势，包括主要贡献者、热门修改领域
2. 分析 Issue 热点，包括问题类型分布、用户反馈热点
3. 分析 Commit 活跃度，包括提交频率、代码变更热点
4. 综合以上信息，生成项目整体动态总结
5. 使用 Markdown 格式，语言为中文"""

    async def _get_llm_config(self, provider: str | None = None) -> LLMProviderConfig:
        """获取 LLM 配置"""
        stmt = select(LLMProviderConfig)

        if provider:
            stmt = stmt.where(LLMProviderConfig.provider == provider)
        else:
            # 获取激活的 provider（is_active=True 的 provider）
            stmt = stmt.where(LLMProviderConfig.is_active)

        stmt = stmt.limit(1)
        result = await self.db.execute(stmt)
        config = result.scalar_one_or_none()

        if not config:
            raise ValueError("No active LLM provider configured. Please set a provider as 'is_active' in the LLM Provider Config page.")

        if not config.api_key:
            raise ValueError(f"API Key not configured for provider: {config.provider}. Please configure API Key in the LLM Provider Config page.")

        return config

    def _build_prompt(self, project: str, daily_data: DailyData, summary_date: date) -> str:
        """构建 LLM 提示词"""
        user_data = f"""日期：{summary_date.strftime('%Y-%m-%d')}
项目：{project}

## PR 数据（共 {len(daily_data.prs)} 个）
"""
        for pr in daily_data.prs[:20]:  # 限制数量，避免 prompt 过长
            user_data += f"- #{pr.get('pr_number', pr.get('number'))}: {pr.get('title', '')} by {pr.get('author', '')}\n"

        user_data += f"\n## Issue 数据（共 {len(daily_data.issues)} 个）\n"
        for issue in daily_data.issues[:20]:
            user_data += f"- #{issue.get('issue_number', issue.get('number'))}: {issue.get('title', '')} by {issue.get('author', '')}\n"

        user_data += f"\n## Commit 数据（共 {len(daily_data.commits)} 个）\n"
        for commit in daily_data.commits[:30]:
            user_data += f"- {commit.get('short_sha', '')}: {commit.get('message', '')} by {commit.get('author', '')}\n"

        user_data += "\n请根据以上数据生成项目动态总结和分析。"
        return user_data

    async def _save_summary(self, **kwargs) -> SummaryResult:
        """保存总结到文件存储（使用 upsert：已存在的文件更新，新文件创建）"""
        project = kwargs.get('project')
        summary_date = kwargs.get('summary_date')

        # 准备元数据
        metadata = {
            "has_data": kwargs.get('has_data', False),
            "pr_count": kwargs.get('pr_count', 0),
            "issue_count": kwargs.get('issue_count', 0),
            "commit_count": kwargs.get('commit_count', 0),
            "llm_provider": kwargs.get('llm_provider'),
            "llm_model": kwargs.get('llm_model'),
            "prompt_tokens": kwargs.get('prompt_tokens'),
            "completion_tokens": kwargs.get('completion_tokens'),
            "generation_time_seconds": kwargs.get('generation_time_seconds'),
            "status": kwargs.get('status', 'success'),
            "error_message": kwargs.get('error_message'),
            "generated_at": datetime.now(UTC).isoformat(),
            "regenerated_at": datetime.now(UTC).isoformat(),
        }

        # 保存到文件
        await self.file_store.save_summary(
            project=project,
            data_date=summary_date,
            summary_markdown=kwargs.get('summary_markdown', ''),
            metadata=metadata
        )

        # 返回 SummaryResult
        return SummaryResult(
            project=project,
            date=summary_date,
            summary_markdown=kwargs.get('summary_markdown', ''),
            has_data=kwargs.get('has_data', False),
            pr_count=kwargs.get('pr_count', 0),
            issue_count=kwargs.get('issue_count', 0),
            commit_count=kwargs.get('commit_count', 0),
            llm_provider=kwargs.get('llm_provider', ''),
            llm_model=kwargs.get('llm_model', ''),
            prompt_tokens=kwargs.get('prompt_tokens'),
            completion_tokens=kwargs.get('completion_tokens'),
            generation_time_seconds=kwargs.get('generation_time_seconds'),
            status=kwargs.get('status', 'success'),
        )

    async def _save_error_summary(self, project: str, summary_date: date, error_message: str):
        """保存错误状态的总结到文件存储"""
        metadata = {
            "has_data": False,
            "status": "failed",
            "error_message": error_message,
            "regenerated_at": datetime.now(UTC).isoformat(),
        }

        await self.file_store.save_summary(
            project=project,
            data_date=summary_date,
            summary_markdown=f"生成失败：{error_message}",
            metadata=metadata
        )
