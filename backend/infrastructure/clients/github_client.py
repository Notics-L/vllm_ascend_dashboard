"""
GitHub API 客户端
提供 GitHub Actions 相关 API 的异步访问，处理速率限制和重试
"""
import asyncio
import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from infrastructure.core.config import settings

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    """GitHub API 调用异常"""
    pass


class GitHubAuthenticationError(GitHubAPIError):
    """GitHub credential rejected (HTTP 401)."""

    pass


class GitHubRateLimitError(GitHubAPIError):
    """GitHub API 速率限制异常"""
    pass


class GitHubClient:
    """
    GitHub API 异步客户端

    特性：
    - 自动处理速率限制
    - 指数退避重试
    - 请求日志记录
    """

    BASE_URL = "https://api.github.com"
    API_VERSION = "2022-11-28"

    # 速率限制阈值（保留 10% 的余量）
    RATE_LIMIT_THRESHOLD = 500

    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0  # 秒
    RETRY_BACKOFF = 2.0  # 指数退避因子

    def __init__(self, token: str, owner: str | None = None, repo: str | None = None):
        """
        初始化 GitHub 客户端

        Args:
            token: GitHub Personal Access Token
            owner: GitHub 组织名
            repo: 仓库名
        """
        self.token = token
        self.owner = owner or settings.GITHUB_OWNER or "vllm-project"
        self.repo = repo or settings.GITHUB_REPO or "vllm-ascend"

        # 速率限制状态
        self.rate_limit_remaining = 5000
        self.rate_limit_reset: datetime | None = None

        # 创建 HTTP 客户端
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=self._get_headers(),
            timeout=httpx.Timeout(30.0, connect=10.0, read=60.0, write=30.0),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

        logger.info(f"GitHubClient initialized for {self.owner}/{self.repo}")

    def _get_headers(self) -> dict[str, str]:
        """获取请求头"""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
            "User-Agent": f"vllm-ascend-dashboard/{self.API_VERSION}",
        }

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        发送 HTTP 请求，带重试和速率限制处理

        Args:
            method: HTTP 方法
            url: 请求路径
            params: 查询参数
            data: 请求体数据

        Returns:
            响应 JSON 数据

        Raises:
            GitHubRateLimitError: 速率限制
            GitHubAPIError: API 调用失败
        """
        retry_count = 0
        last_error: Exception | None = None

        while retry_count < self.MAX_RETRIES:
            try:
                # 检查速率限制（在每次请求前）
                if self.rate_limit_remaining < self.RATE_LIMIT_THRESHOLD:
                    if self.rate_limit_reset and datetime.now(UTC) < self.rate_limit_reset:
                        raise GitHubRateLimitError(
                            f"GitHub API rate limit exceeded. Resets at {self.rate_limit_reset}"
                        )
                    else:
                        # rate_limit_reset 已过期，重置计数器允许请求继续
                        logger.info(f"Rate limit reset time has passed, resetting rate_limit_remaining from {self.rate_limit_remaining} to 5000")
                        self.rate_limit_remaining = 5000
                        self.rate_limit_reset = None

                response = await self.client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                )

                # 更新速率限制信息
                self._update_rate_limit(response)

                # 处理响应 - 速率限制检查
                if response.status_code == 403:
                    # 检查是否是速率限制
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    if remaining == "0":
                        reset_timestamp = response.headers.get("X-RateLimit-Reset")
                        if reset_timestamp:
                            try:
                                reset_time = datetime.fromtimestamp(
                                    int(reset_timestamp), tz=UTC
                                )
                                raise GitHubRateLimitError(
                                    f"GitHub API rate limit exceeded. Resets at {reset_time}"
                                )
                            except (ValueError, TypeError):
                                # 无法解析时间戳，使用默认消息
                                raise GitHubRateLimitError("GitHub API rate limit exceeded")
                        raise GitHubRateLimitError("GitHub API rate limit exceeded")

                if response.status_code == httpx.codes.UNAUTHORIZED:
                    raise GitHubAuthenticationError(
                        "GitHub API authentication failed (HTTP 401)"
                    )

                response.raise_for_status()

                # 返回 JSON 数据
                if response.status_code == 204:
                    return {}
                return response.json()

            except (GitHubAuthenticationError, GitHubRateLimitError):
                # 速率限制不重试
                raise

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"HTTP error: {e.response.status_code} - {e.response.text}")

                # 4xx 错误不重试
                if 400 <= e.response.status_code < 500:
                    break

            except httpx.RequestError as e:
                last_error = e
                logger.warning(f"Request error: {e}")

            except Exception as e:
                last_error = e
                logger.warning(f"Unexpected error: {e}")

            # 指数退避
            retry_count += 1
            if retry_count < self.MAX_RETRIES:
                delay = self.RETRY_DELAY * (self.RETRY_BACKOFF ** (retry_count - 1))
                logger.info(f"Retrying in {delay:.1f}s (attempt {retry_count}/{self.MAX_RETRIES})")
                await asyncio.sleep(delay)

        # 所有重试失败
        error_msg = f"GitHub API request failed after {retry_count} attempts"
        if last_error:
            error_msg += f": {last_error}"
        raise GitHubAPIError(error_msg)

    def _update_rate_limit(self, response: httpx.Response) -> None:
        """更新速率限制信息"""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset_timestamp = response.headers.get("X-RateLimit-Reset")

        if remaining:
            self.rate_limit_remaining = int(remaining)
            logger.debug(f"GitHub API rate limit remaining: {self.rate_limit_remaining}")

        if reset_timestamp:
            self.rate_limit_reset = datetime.fromtimestamp(
                int(reset_timestamp), tz=UTC
            )

    async def get_workflow_runs(
        self,
        workflow_id_or_name: str,
        status: str | None = None,
        branch: str | None = None,
        event: str | None = None,
        created: str | None = None,
        per_page: int = 100,
        page: int = 1,
        days_back: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        获取 workflow 运行历史

        Args:
            workflow_id_or_name: workflow ID 或文件名
            status: 状态过滤 (completed, in_progress, queued, action_required)
            branch: 分支过滤
            event: 事件过滤 (push, pull_request, schedule, etc.)
            created: 创建时间过滤 (GitHub API 格式：>=YYYY-MM-DD, <=YYYY-MM-DD)
            per_page: 每页数量 (最大 100)
            page: 页码
            days_back: 从多少天前开始采集（自动转换为 created 参数）

        Returns:
            workflow runs 列表
        """
        url = f"/repos/{self.owner}/{self.repo}/actions/workflows/{workflow_id_or_name}/runs"

        params = {
            "per_page": min(per_page, 100),
            "page": page,
        }

        if status:
            params["status"] = status
        if branch:
            params["branch"] = branch
        if event:
            params["event"] = event

        # 处理时间过滤：优先使用 created 参数，如果提供了 days_back 则自动生成
        if created:
            params["created"] = created
        elif days_back:
            from datetime import datetime, timedelta
            days_ago = datetime.now(UTC) - timedelta(days=days_back)
            params["created"] = f">={days_ago.strftime('%Y-%m-%d')}"

        logger.info(f"Fetching workflow runs for {workflow_id_or_name} (page {page}, created={params.get('created')})")

        result = await self._request("GET", url, params=params)
        return result.get("workflow_runs", [])

    async def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        """
        获取单次 workflow run 详情

        Args:
            run_id: workflow run ID

        Returns:
            workflow run 详情
        """
        url = f"/repos/{self.owner}/{self.repo}/actions/runs/{run_id}"
        logger.info(f"Fetching workflow run {run_id}")
        return await self._request("GET", url)

    async def get_repository_workflow_runs(self, *, page: int = 1, per_page: int = 100) -> list[dict[str, Any]]:
        """List recent runs for this repository, for local metadata backfills."""
        result = await self._request(
            "GET", f"/repos/{self.owner}/{self.repo}/actions/runs",
            params={"page": page, "per_page": min(per_page, 100)},
        )
        return result.get("workflow_runs", [])

    async def get_job_list(self, run_id: int) -> list[dict[str, Any]]:
        """
        获取 workflow run 的 job 列表（支持分页，获取所有 jobs）

        Args:
            run_id: workflow run ID

        Returns:
            job 列表
        """
        url = f"/repos/{self.owner}/{self.repo}/actions/runs/{run_id}/jobs"
        logger.info(f"Fetching jobs for run {run_id}")

        all_jobs = []
        page = 1
        per_page = 100  # GitHub API 最大每页数量

        while True:
            params = {"per_page": per_page, "page": page}
            result = await self._request("GET", url, params=params)
            jobs = result.get("jobs", [])

            if not jobs:
                break

            all_jobs.extend(jobs)
            logger.info(f"Fetched page {page} with {len(jobs)} jobs for run {run_id} (total: {len(all_jobs)})")

            # 如果返回的 jobs 数量小于 per_page，说明已经是最后一页
            if len(jobs) < per_page:
                break

            page += 1

        logger.info(f"Total {len(all_jobs)} jobs fetched for run {run_id}")
        return all_jobs

    async def get_artifacts(
        self,
        workflow_run_id: int | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """
        获取 artifacts 列表

        Args:
            workflow_run_id: workflow run ID（可选，不传则获取所有 artifacts）
            per_page: 每页数量

        Returns:
            artifacts 列表
        """
        if workflow_run_id:
            url = f"/repos/{self.owner}/{self.repo}/actions/runs/{workflow_run_id}/artifacts"
        else:
            url = f"/repos/{self.owner}/{self.repo}/actions/artifacts"

        page = 1
        page_size = min(per_page, 100)
        artifacts: list[dict[str, Any]] = []
        logger.info(f"Fetching artifacts for run {workflow_run_id}")

        # A run can produce more than 100 matrix artifacts.  Returning only
        # the first page makes the local evidence cache look complete while
        # silently omitting the artifact belonging to the failed job.
        while True:
            result = await self._request(
                "GET", url, params={"per_page": page_size, "page": page}
            )
            items = result.get("artifacts", [])
            if not items:
                break
            artifacts.extend(items)
            if len(items) < page_size:
                break
            page += 1

        logger.info("Fetched %d artifacts for run %s", len(artifacts), workflow_run_id)
        return artifacts

    async def list_artifacts(
        self,
        workflow_run_id: int,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """
        获取指定 workflow run 的 artifacts 列表（别名方法）

        Args:
            workflow_run_id: workflow run ID
            per_page: 每页数量

        Returns:
            artifacts 列表
        """
        return await self.get_artifacts(workflow_run_id, per_page)

    async def download_artifact(self, artifact_id: int) -> bytes:
        """
        下载 artifact 文件（ZIP 格式）

        GitHub Actions artifacts 总是以 ZIP 格式下载，即使只包含单个文件。

        Args:
            artifact_id: artifact ID

        Returns:
            ZIP 文件二进制数据
        """
        # 先获取 artifact 信息（返回 JSON）
        artifact_info = await self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/actions/artifacts/{artifact_id}"
        )

        # 从 artifact 信息中获取 archive_download_url
        download_url = artifact_info.get("archive_download_url", "")
        if not download_url:
            raise GitHubAPIError(f"No download URL for artifact {artifact_id}")

        logger.info(f"Downloading artifact {artifact_id} from {download_url}")

        # 下载 ZIP 文件（使用临时客户端，允许重定向）
        # 注意：下载 URL 不是 GitHub API，所以不能使用 self.client
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0),
            headers={"Authorization": f"Bearer {self.token}"}
        ) as download_client:
            response = await download_client.get(download_url)
            response.raise_for_status()
            return response.content

    async def get_artifact_info(self, artifact_id: int) -> dict[str, Any]:
        """
        获取 artifact 元数据信息

        Args:
            artifact_id: artifact ID

        Returns:
            artifact 信息，包括 name, size_in_bytes, created_at 等
        """
        return await self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/actions/artifacts/{artifact_id}"
        )

    async def get_job_logs(self, job_id: int) -> str:
        """
        获取 job 的日志内容

        Args:
            job_id: job ID

        Returns:
            日志文本内容
        """
        url = f"/repos/{self.owner}/{self.repo}/actions/jobs/{job_id}/logs"
        logger.info(f"Fetching logs for job {job_id}")

        try:
            # 日志接口返回的是纯文本，不是 JSON
            response = await self.client.get(url)
            # Actions log endpoints can return a signed cross-origin URL.  Some
            # proxy/client combinations expose that initial 302 to this caller.
            if response.is_redirect and (redirect_url := response.headers.get("location")):
                response = await self.client.get(redirect_url, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Logs not available for job {job_id}")
                return ""
            elif e.response.status_code == 403:
                logger.warning(f"Logs access forbidden for job {job_id} (may have expired)")
                return ""
            raise
        except httpx.RequestError as e:
            logger.error(f"Network error fetching logs for job {job_id}: {e}")
            return ""
        except Exception as e:
            logger.error(f"Failed to fetch logs for job {job_id}: {e}")
            return ""

    async def get_repository_file_content(self, path: str, ref: str) -> str:
        """Read a text file at an immutable Git commit SHA."""
        result = await self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            params={"ref": ref},
        )
        encoded = result.get("content")
        if not isinstance(encoded, str) or result.get("encoding") != "base64":
            raise GitHubAPIError(f"Repository file is not Base64 text: {path}@{ref}")
        return base64.b64decode(encoded).decode("utf-8")

    async def find_repository_paths_by_filename(self, filename: str, ref: str) -> list[str]:
        """Find candidate paths for a benchmark YAML in one commit tree."""
        result = await self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/git/trees/{ref}",
            params={"recursive": "1"},
        )
        return [
            entry["path"]
            for entry in result.get("tree", [])
            if entry.get("type") == "blob" and entry.get("path", "").endswith(f"/{filename}")
        ]

    async def get_rate_limit_status(self) -> dict[str, Any]:
        """
        获取当前速率限制状态

        Returns:
            速率限制信息
        """
        result = await self._request("GET", "/rate_limit")
        return result.get("resources", {})

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        await self.client.aclose()
        logger.info("GitHubClient closed")

    async def search_issues(self, query: str, per_page: int = 100, sort: str | None = None, order: str = "desc") -> dict[str, Any]:
        """
        通过 GitHub Search API 搜索 issues/PRs

        Args:
            query: 搜索查询语句（如 "repo:vllm-project/vllm-ascend is:issue is:open label:bug"）
            per_page: 每页数量
            sort: 排序字段（如 "updated"）
            order: 排序方向

        Returns:
            搜索结果（含 total_count 和 items）
        """
        params: dict[str, Any] = {"q": query, "per_page": per_page, "order": order}
        if sort:
            params["sort"] = sort
        return await self._request("GET", "/search/issues", params=params)

    async def get_repo_info(self) -> dict[str, Any]:
        """
        获取当前仓库的基本信息（stars, forks, open issues 等）

        Returns:
            仓库信息字典
        """
        return await self._request("GET", f"/repos/{self.owner}/{self.repo}")

    async def __aenter__(self) -> "GitHubClient":
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """异步上下文管理器出口"""
        await self.close()

    # ============ GitHub 动态数据相关 API ============

    async def get_recent_pull_requests(self, days: int = 1) -> list[dict[str, Any]]:
        """
        获取最近 N 天的 Pull Requests（只返回打开状态的 PR）

        Args:
            days: 获取最近 N 天的数据

        Returns:
            Pull Requests 列表
        """
        url = f"/repos/{self.owner}/{self.repo}/pulls"
        params = {
            "state": "open",  # 只获取打开状态的 PR
            "sort": "created",
            "direction": "desc",
            "per_page": 100,
        }

        logger.info(f"Fetching recent pull requests (last {days} days, state=open)")
        result = await self._request("GET", url, params=params)

        # 过滤最近 N 天的数据
        cutoff_date = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_date = cutoff_date - timedelta(days=days)

        prs = []
        for pr in result:
            created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            if created_at >= cutoff_date:
                prs.append(pr)

        return prs

    async def get_recent_issues(self, days: int = 1) -> list[dict[str, Any]]:
        """
        获取最近 N 天的 Issues

        Args:
            days: 获取最近 N 天的数据

        Returns:
            Issues 列表
        """
        url = f"/repos/{self.owner}/{self.repo}/issues"
        params = {
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": 100,
        }

        logger.info(f"Fetching recent issues (last {days} days)")
        result = await self._request("GET", url, params=params)

        # 过滤最近 N 天的数据和 Pull Request（issues 接口包含 PR）
        cutoff_date = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_date = cutoff_date - timedelta(days=days)

        issues = []
        for issue in result:
            # 跳过 Pull Request
            if "pull_request" in issue:
                continue
            created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
            if created_at >= cutoff_date:
                issues.append(issue)

        return issues

    async def get_latest_release(self) -> dict[str, Any]:
        """
        获取最新发布的版本（包括 pre-release）

        Returns:
            最新 Release 信息（包含 latest 和 pre-release）
        """
        # 获取最新版本（stable）
        latest_release = None
        prerelease_release = None

        try:
            url = f"/repos/{self.owner}/{self.repo}/releases/latest"
            latest_release = await self._request("GET", url)
        except GitHubAPIError as e:
            if "404" not in str(e):
                raise

        # 获取 pre-release 版本
        try:
            url = f"/repos/{self.owner}/{self.repo}/releases"
            params = {"per_page": 10}
            result = await self._request("GET", url, params=params)
            # 找到最新的 pre-release
            for release in result:
                if release.get("prerelease", False):
                    prerelease_release = release
                    break
        except GitHubAPIError:
            pass

        return {
            "latest": latest_release,
            "prerelease": prerelease_release,
        }

    async def get_recent_commits(self, days: int = 1, sha: str | None = None) -> list[dict[str, Any]]:
        """
        获取最近 N 天的 Commits

        Args:
            days: 获取最近 N 天的数据
            sha: 分支名或 commit SHA（可选，默认主分支）

        Returns:
            Commits 列表
        """
        url = f"/repos/{self.owner}/{self.repo}/commits"
        params = {
            "per_page": 100,
        }
        if sha:
            params["sha"] = sha

        logger.info(f"Fetching recent commits (last {days} days, sha={sha})")
        result = await self._request("GET", url, params=params)

        # 过滤最近 N 天的数据
        cutoff_date = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_date = cutoff_date - timedelta(days=days)

        commits = []
        for commit in result:
            commit_date = datetime.fromisoformat(commit["commit"]["committer"]["date"].replace("Z", "+00:00"))
            if commit_date >= cutoff_date:
                commits.append(commit)

        return commits

    async def get_workflow_run_history(
        self,
        workflow_id_or_name: str,
        per_page: int = 10,
    ) -> list[dict[str, Any]]:
        """
        获取 workflow 最近 N 次运行记录（用于失败分析的历史对比）

        Args:
            workflow_id_or_name: workflow ID 或文件名
            per_page: 获取最近几次运行（默认10）

        Returns:
            workflow runs 列表，每个包含 id, run_number, status, conclusion, head_sha, head_commit, created_at, event
        """
        url = f"/repos/{self.owner}/{self.repo}/actions/workflows/{workflow_id_or_name}/runs"
        params = {
            "per_page": min(per_page, 100),
            "page": 1,
            "status": "completed",
        }
        logger.info(f"Fetching workflow run history for {workflow_id_or_name} (per_page={per_page})")
        result = await self._request("GET", url, params=params)
        return result.get("workflow_runs", [])

    async def get_compare_commits(
        self,
        base_sha: str,
        head_sha: str,
    ) -> dict[str, Any]:
        """
        获取两个 commit 之间的对比信息

        Args:
            base_sha: 基准 commit SHA
            head_sha: 目标 commit SHA

        Returns:
            compare 结果，包含 commits 列表和 files 变更
        """
        url = f"/repos/{self.owner}/{self.repo}/compare/{base_sha}...{head_sha}"
        logger.info(f"Fetching compare between {base_sha}...{head_sha}")
        return await self._request("GET", url)

    # ============ 每日总结相关 API ============

    async def get_pull_requests_by_date_range(
        self,
        owner: str,
        repo: str,
        start_time: datetime,
        end_time: datetime
    ) -> list[dict[str, Any]]:
        """
        获取指定时间范围内的 PR 列表

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            start_time: 开始时间（UTC 时间）
            end_time: 结束时间（UTC 时间）

        Returns:
            PR 列表，每个 PR 包含 commits 信息
        """
        url = f"/repos/{owner}/{repo}/pulls"
        params = {
            "state": "all",  # 获取所有状态的 PR
            "sort": "created",
            "direction": "desc",
            "per_page": 100,
            "page": 1,
        }

        logger.info(f"Fetching pull requests by date range ({start_time} to {end_time})")

        # 将 start_time 和 end_time 转换为 UTC 进行比较
        start_time_utc = start_time.astimezone(UTC) if start_time.tzinfo else start_time.replace(tzinfo=UTC)
        end_time_utc = end_time.astimezone(UTC) if end_time.tzinfo else end_time.replace(tzinfo=UTC)

        prs = []
        page = 1

        while True:
            params["page"] = page
            result = await self._request("GET", url, params=params)

            if not result:
                break

            found_in_range = False
            for pr in result:
                # GitHub API 返回的 created_at 是 UTC 时间（格式：2026-04-08T12:34:56Z）
                created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
                created_at_utc = created_at.astimezone(UTC)

                if created_at_utc < start_time_utc:
                    # 因为是按 created 降序排列，一旦找到早于 start_time 的 PR，后面的都更早，可以停止
                    logger.info(f"Stopping pagination at page {page} - PR {pr['number']} created at {created_at_utc} is before start time {start_time_utc}")
                    found_in_range = False
                    break

                if start_time_utc <= created_at_utc <= end_time_utc:
                    # 获取该 PR 的 commits 列表
                    commits = await self.get_pr_commits(owner, repo, pr["number"])
                    pr["commits"] = commits
                    prs.append(pr)
                    found_in_range = True

            if not found_in_range and page > 1:
                # 如果这一页没有找到在时间范围内的 PR，且不是第一页，说明已经遍历完了
                break

            if len(result) < 100:
                # 如果这一页的结果少于 100，说明是最后一页
                break

            page += 1
            logger.info(f"Fetching page {page} for pull requests")

        logger.info(f"Found {len(prs)} pull requests in date range")
        return prs

    async def get_pull_requests_by_updated_range(
        self,
        owner: str,
        repo: str,
        start_time: datetime,
        end_time: datetime,
        max_items: int | None = None,
        resume_before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch PR summaries incrementally, ordered by GitHub ``updated_at``.

        The regular date-range endpoint is ordered by ``created`` and therefore
        has to walk every page created in the lookback window.  Scheduled PR
        syncs use this endpoint instead: once an item is older than the
        watermark, pagination can stop.  Details (reviews/files/commits) are
        deliberately not fetched here; the collector fetches them only for
        the bounded set of summaries it will upsert.
        """
        url = f"/repos/{owner}/{repo}/pulls"
        page_size = 100
        limit = max_items if max_items and max_items > 0 else None
        start = start_time.astimezone(UTC) if start_time.tzinfo else start_time.replace(tzinfo=UTC)
        end = end_time.astimezone(UTC) if end_time.tzinfo else end_time.replace(tzinfo=UTC)
        resume = (
            resume_before.astimezone(UTC)
            if resume_before and resume_before.tzinfo
            else resume_before.replace(tzinfo=UTC)
            if resume_before
            else None
        )
        results: list[dict[str, Any]] = []
        page = 1

        while True:
            result = await self._request(
                "GET",
                url,
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": page_size,
                    "page": page,
                },
            )
            if not result:
                break

            reached_watermark = False
            for pr in result:
                updated_raw = pr.get("updated_at") or pr.get("created_at")
                try:
                    updated_at = datetime.fromisoformat(
                        str(updated_raw).replace("Z", "+00:00")
                    ).astimezone(UTC)
                except (TypeError, ValueError):
                    logger.warning(
                        "Skipping PR #%s with invalid updated_at=%r",
                        pr.get("number"),
                        updated_raw,
                    )
                    continue

                if updated_at < start:
                    reached_watermark = True
                    break
                # A capped backlog uses the previous batch's oldest
                # timestamp as an exclusive continuation cursor.  Newer PRs
                # are skipped until the backlog is drained, then normal
                # watermark+overlap processing resumes.
                if resume is not None:
                    if updated_at > resume:
                        continue
                if updated_at <= end:
                    results.append(pr)
                    if limit is not None and len(results) >= limit:
                        break

            if reached_watermark or (limit is not None and len(results) >= limit):
                break
            if len(result) < page_size:
                break
            page += 1

        logger.info(
            "Found %d pull requests updated between %s and %s (max_items=%s)",
            len(results),
            start.isoformat(),
            end.isoformat(),
            limit,
        )
        return results[:limit] if limit is not None else results

    async def get_pr_commits(
        self,
        owner: str,
        repo: str,
        pr_number: int
    ) -> list[dict[str, Any]]:
        """
        获取指定 PR 的所有 commits

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            pr_number: PR 编号

        Returns:
            commits 列表
        """
        url = f"/repos/{owner}/{repo}/pulls/{pr_number}/commits"
        params = {"per_page": 100}
        logger.info(f"Fetching commits for PR #{pr_number}")
        result = await self._request("GET", url, params=params)
        return result

    async def get_user_email(self, username: str, owner: str, repo: str) -> str | None:
        """
        Get a user's email address. Tries commit history first (most reliable),
        falls back to user profile.

        Args:
            username: GitHub login
            owner: repo owner
            repo: repo name

        Returns:
            Email string or None
        """
        # Strategy 1: look for commits by this user in the repo (git emails always present)
        try:
            url = f"/repos/{owner}/{repo}/commits"
            params = {"author": username, "per_page": 1}
            commits = await self._request("GET", url, params=params)
            if commits:
                email = (commits[0].get("commit", {}).get("author", {}).get("email", ""))
                if email:
                    return email
        except Exception:
            pass

        # Strategy 2: user profile (public_email often null)
        try:
            user = await self._request("GET", f"/users/{username}")
            email = user.get("email") or user.get("public_email")
            if email:
                return email
        except Exception:
            pass

        return None

    async def get_pr_detail(
        self,
        owner: str,
        repo: str,
        pr_number: int
    ) -> dict[str, Any]:
        """
        获取 PR 详细信息

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            pr_number: PR 编号

        Returns:
            PR 详细信息
        """
        url = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        logger.info(f"Fetching PR #{pr_number} detail")
        return await self._request("GET", url)

    async def get_pr_reviews(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        """
        获取指定 PR 的所有 reviews

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            pr_number: PR 编号

        Returns:
            reviews 列表
        """
        url = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        params = {"per_page": 100}
        logger.info(f"Fetching reviews for PR #{pr_number}")

        all_reviews = []
        page = 1

        while True:
            params["page"] = page
            result = await self._request("GET", url, params=params)
            reviews = result if isinstance(result, list) else result.get("reviews", result.get("items", []))

            if not reviews:
                break

            all_reviews.extend(reviews)

            if len(reviews) < 100:
                break

            page += 1

        logger.info(f"Total {len(all_reviews)} reviews fetched for PR #{pr_number}")
        return all_reviews

    async def get_pr_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[dict[str, Any]]:
        """
        获取指定 PR 的所有变更文件

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            pr_number: PR 编号

        Returns:
            files 列表，每个包含 filename, additions, deletions, changes, status 等
        """
        url = f"/repos/{owner}/{repo}/pulls/{pr_number}/files"
        params = {"per_page": 100}
        logger.info(f"Fetching files for PR #{pr_number}")

        all_files = []
        page = 1

        while True:
            params["page"] = page
            result = await self._request("GET", url, params=params)
            files = result if isinstance(result, list) else []

            if not files:
                break

            all_files.extend(files)

            if len(files) < 100:
                break

            page += 1

        logger.info(f"Total {len(all_files)} files fetched for PR #{pr_number}")
        return all_files

    async def get_check_runs_for_sha(
        self,
        owner: str,
        repo: str,
        sha: str,
    ) -> list[dict[str, Any]]:
        """
        获取指定 commit SHA 的 check runs（CI 状态）

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            sha: commit SHA

        Returns:
            check runs 列表
        """
        url = f"/repos/{owner}/{repo}/commits/{sha}/check-runs"
        params = {"per_page": 100}
        logger.info(f"Fetching check runs for SHA {sha[:8]}")
        result = await self._request("GET", url, params=params)
        return result.get("check_runs", [])

    async def get_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int
    ) -> dict[str, Any]:
        """
        获取 Issue 详细信息

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            issue_number: Issue 编号

        Returns:
            Issue 详细信息
        """
        url = f"/repos/{owner}/{repo}/issues/{issue_number}"
        logger.info(f"Fetching Issue #{issue_number} detail")
        return await self._request("GET", url)

    async def get_issues_by_date_range(
        self,
        owner: str,
        repo: str,
        start_time: datetime,
        end_time: datetime
    ) -> list[dict[str, Any]]:
        """
        获取指定时间范围内的 Issue 列表

        Args:
            owner: GitHub 组织名
            repo: 仓库名
            start_time: 开始时间（UTC 时间）
            end_time: 结束时间（UTC 时间）

        Returns:
            Issue 列表
        """
        url = f"/repos/{owner}/{repo}/issues"
        params = {
            "state": "all",
            "sort": "created",
            "direction": "desc",
            "per_page": 100,
            "page": 1,
        }

        logger.info(f"Fetching issues by date range ({start_time} to {end_time})")

        # 将 start_time 和 end_time 转换为 UTC 进行比较
        start_time_utc = start_time.astimezone(UTC) if start_time.tzinfo else start_time.replace(tzinfo=UTC)
        end_time_utc = end_time.astimezone(UTC) if end_time.tzinfo else end_time.replace(tzinfo=UTC)

        issues = []
        page = 1

        while True:
            params["page"] = page
            result = await self._request("GET", url, params=params)

            if not result:
                break

            found_in_range = False
            for issue in result:
                if "pull_request" in issue:  # 跳过 PR
                    continue

                # GitHub API 返回的 created_at 是 UTC 时间（格式：2026-04-08T12:34:56Z）
                created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
                created_at_utc = created_at.astimezone(UTC)

                if created_at_utc < start_time_utc:
                    # 因为是按 created 降序排列，一旦找到早于 start_time 的 issue，后面的都更早，可以停止
                    logger.info(f"Stopping pagination at page {page} - Issue {issue['number']} created at {created_at_utc} is before start time {start_time_utc}")
                    found_in_range = False
                    break

                if start_time_utc <= created_at_utc <= end_time_utc:
                    issues.append(issue)
                    found_in_range = True

            if not found_in_range and page > 1:
                # 如果这一页没有找到在时间范围内的 issue，且不是第一页，说明已经遍历完了
                break

            if len(result) < 100:
                # 如果这一页的结果少于 100，说明是最后一页
                break

            page += 1
            logger.info(f"Fetching page {page} for issues")

        logger.info(f"Found {len(issues)} issues in date range")
        return issues
