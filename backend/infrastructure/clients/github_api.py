"""
GitHub API Service
Provides GitHub API integration for issues, PRs, and workflows
"""
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.config import settings
from infrastructure.core.github_credentials import global_github_credential

logger = logging.getLogger(__name__)


class GitHubAPIService:
    """GitHub API 服务"""

    def __init__(self):
        self.owner = settings.GITHUB_OWNER or "vllm-project"
        self.repo = settings.GITHUB_REPO or "vllm-ascend"
        self._credential = global_github_credential()
        self.headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{self.owner}/{self.repo}-dashboard",
        }

    async def _request(self, method: str, endpoint: str, **kwargs) -> dict[str, Any] | None:
        """发送 GitHub API 请求"""
        try:
            async with GitHubAsyncClient(
                audit_source="github_api", credential=self._credential, headers=self.headers, timeout=30.0
            ) as client:
                response = await client.request(method, endpoint, **kwargs)
                response.raise_for_status()
                # Some endpoints return empty response (e.g., rerun workflow)
                if response.status_code == 201 or not response.content:
                    return {"success": True}
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("GitHub API returned HTTP %s", e.response.status_code)
            return None
        except Exception as e:
            logger.error("GitHub API request failed (%s)", type(e).__name__)
            return None

    async def get_stale_issues_and_prs(self, days: int = 7) -> dict[str, list[dict[str, Any]]]:
        """获取超期未 review 的 issues 和 PRs"""
        threshold_date = datetime.now(UTC) - timedelta(days=days)

        issues = []
        prs = []
        page = 1
        per_page = 100

        while True:
            endpoint = f"/repos/{self.owner}/{self.repo}/issues"
            params = {
                "state": "open",
                "sort": "updated",
                "direction": "asc",
                "per_page": per_page,
                "page": page,
            }

            result = await self._request("GET", endpoint, params=params)
            if not result:
                break

            batch_items = result if isinstance(result, list) else []
            if not batch_items:
                break

            for item in batch_items:
                updated_at = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))

                if updated_at >= threshold_date:
                    continue  # Not stale yet

                days_stale = (datetime.now(UTC) - updated_at).days
                labels = [label["name"] for label in item.get("labels", [])]

                entry = {
                    "number": item["number"],
                    "title": item["title"],
                    "html_url": item["html_url"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "days_stale": days_stale,
                    "author": item["user"]["login"] if item.get("user") else None,
                    "labels": labels,
                    "assignees": [a["login"] for a in item.get("assignees", [])],
                    "comments": item.get("comments", 0),
                }

                # Check if it's a PR
                if "pull_request" in item:
                    # Get PR specific info
                    pr_info = item.get("pull_request", {})
                    entry["type"] = "pr"
                    entry["pr_state"] = pr_info.get("state", "open")
                    entry["draft"] = item.get("draft", False)
                    prs.append(entry)
                else:
                    entry["type"] = "issue"
                    issues.append(entry)

            if len(batch_items) < per_page:
                break

            page += 1

        return {"issues": issues, "prs": prs}

    async def get_pr(self, pr_number: int) -> dict[str, Any] | None:
        """获取 PR 信息"""
        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}"
        return await self._request("GET", endpoint)

    async def rerun_workflow_run(self, run_id: int) -> bool:
        """重新运行 workflow"""
        endpoint = f"/repos/{self.owner}/{self.repo}/actions/runs/{run_id}/rerun"
        logger.info(f"Attempting to rerun workflow {run_id} for {self.owner}/{self.repo}")
        try:
            async with GitHubAsyncClient(
                audit_source="github_api", credential=self._credential, headers=self.headers, timeout=30.0
            ) as client:
                response = await client.post(endpoint)
                logger.info("GitHub rerun returned HTTP %s", response.status_code)
                # GitHub returns 201 Created on success
                if response.status_code == 201:
                    logger.info(f"✓ Successfully triggered rerun for workflow {run_id}")
                    return True
                else:
                    logger.error("GitHub workflow %s rerun failed with HTTP %s", run_id, response.status_code)
                    return False
        except Exception as e:
            logger.error("GitHub workflow %s rerun failed (%s)", run_id, type(e).__name__)
            return False

    async def rerun_check_suite(self, check_suite_id: int) -> bool:
        """重新运行 check suite"""
        endpoint = f"/repos/{self.owner}/{self.repo}/check-suites/{check_suite_id}/rerun"
        logger.info(f"Attempting to rerun check suite {check_suite_id}")
        try:
            async with GitHubAsyncClient(
                audit_source="github_api", credential=self._credential, headers=self.headers, timeout=30.0
            ) as client:
                response = await client.post(endpoint)
                # GitHub returns 201 Created on success
                if response.status_code == 201:
                    logger.info(f"✓ Successfully triggered rerun for check suite {check_suite_id}")
                    return True
                else:
                    logger.error("GitHub check suite %s rerun failed with HTTP %s", check_suite_id, response.status_code)
                    return False
        except Exception as e:
            logger.error("GitHub check suite %s rerun failed (%s)", check_suite_id, type(e).__name__)
            return False

    async def get_pr_check_runs(self, pr_number: int) -> list[dict[str, Any]]:
        """获取 PR 的 check runs"""
        endpoint = f"/repos/{self.owner}/{self.repo}/commits/refs/pull/{pr_number}/head/check-runs"
        result = await self._request("GET", endpoint)
        if result:
            return result.get("check_runs", [])
        return []

    async def merge_pr(
        self,
        pr_number: int,
        merge_method: str = "merge",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any] | None:
        """合并 PR"""
        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{pr_number}/merge"
        data = {"merge_method": merge_method}
        if commit_title:
            data["commit_title"] = commit_title
        if commit_message:
            data["commit_message"] = commit_message

        result = await self._request("PUT", endpoint, json=data)
        return result

    async def get_workflow_runs_for_pr(
        self,
        pr_number: int,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取 PR 的 workflow runs

        Args:
            pr_number: PR 编号
            status: 过滤条件（failure, success, in_progress 等），不传则返回所有
        """
        # First, get PR info to get the head ref and sha
        pr_info = await self.get_pr(pr_number)
        if not pr_info:
            logger.warning(f"PR #{pr_number} not found")
            return []

        head_ref = pr_info.get("head", {}).get("ref", "")
        head_sha = pr_info.get("head", {}).get("sha", "")
        logger.info(f"PR #{pr_number} head ref: {head_ref}, head sha: {head_sha}")

        if not head_ref:
            logger.warning(f"Could not get head ref for PR #{pr_number}")
            return []

        endpoint = f"/repos/{self.owner}/{self.repo}/actions/runs"

        # Try multiple approaches to find workflow runs for this PR
        all_pr_runs = []

        # Approach 1: Filter by branch
        try:
            params_branch = {
                "event": "pull_request",
                "per_page": 100,
                "branch": head_ref,
            }
            logger.info(f"Approach 1: Fetching workflow runs for PR #{pr_number} with branch={head_ref}")
            result = await self._request("GET", endpoint, params=params_branch)
            if result:
                runs = result.get("workflow_runs", [])
                logger.info(f"Got {len(runs)} workflow runs for branch {head_ref}")

                # Filter by PR number
                pr_runs = [
                    run for run in runs
                    if run.get("pull_requests", [])
                    and any(pr.get("number") == pr_number for pr in run["pull_requests"])
                ]
                logger.info(f"Found {len(pr_runs)} workflow runs for PR #{pr_number} after filtering by PR number")
                all_pr_runs.extend(pr_runs)
        except Exception as e:
            logger.error(f"Approach 1 failed: {e}")

        # Approach 2: Filter by head_sha if no runs found
        if not all_pr_runs and head_sha:
            try:
                params_all = {
                    "event": "pull_request",
                    "per_page": 100,
                }
                logger.info(f"Approach 2: Fetching all PR workflow runs to filter by head_sha={head_sha}")
                result = await self._request("GET", endpoint, params=params_all)
                if result:
                    runs = result.get("workflow_runs", [])
                    logger.info(f"Got {len(runs)} workflow runs")

                    # Filter by head_sha
                    pr_runs = [
                        run for run in runs
                        if run.get("head_sha") == head_sha
                    ]
                    logger.info(f"Found {len(pr_runs)} workflow runs matching head_sha for PR #{pr_number}")
                    all_pr_runs.extend(pr_runs)
            except Exception as e:
                logger.error(f"Approach 2 failed: {e}")

        # Approach 3: Get all workflow runs and filter by PR number
        if not all_pr_runs:
            try:
                params_all = {
                    "event": "pull_request",
                    "per_page": 100,
                }
                logger.info("Approach 3: Fetching all PR workflow runs to filter by PR number")
                result = await self._request("GET", endpoint, params=params_all)
                if result:
                    runs = result.get("workflow_runs", [])
                    logger.info(f"Got {len(runs)} workflow runs")

                    # Filter by PR number
                    pr_runs = [
                        run for run in runs
                        if run.get("pull_requests", [])
                        and any(pr.get("number") == pr_number for pr in run["pull_requests"])
                    ]
                    logger.info(f"Found {len(pr_runs)} workflow runs for PR #{pr_number}")
                    all_pr_runs.extend(pr_runs)
            except Exception as e:
                logger.error(f"Approach 3 failed: {e}")

        # Remove duplicates by run id
        seen_ids = set()
        unique_runs = []
        for run in all_pr_runs:
            run_id = run.get("id")
            if run_id and run_id not in seen_ids:
                seen_ids.add(run_id)
                unique_runs.append(run)

        logger.info(f"Total unique workflow runs for PR #{pr_number}: {len(unique_runs)}")

        # Filter by conclusion or status if specified
        if status:
            # Try conclusion first (for completed runs)
            filtered = [run for run in unique_runs if run.get("conclusion") == status]
            # If no matches, try status field (for in_progress runs)
            if not filtered:
                filtered = [run for run in unique_runs if run.get("status") == status]
            unique_runs = filtered

        return unique_runs

    async def get_recent_workflow_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取最近的 workflow runs（不限制 PR）"""
        endpoint = f"/repos/{self.owner}/{self.repo}/actions/runs"
        params = {
            "per_page": min(limit, 100),
        }

        result = await self._request("GET", endpoint, params=params)
        if not result:
            return []

        return result.get("workflow_runs", [])

    async def get_releases(self) -> list[dict[str, Any]]:
        """获取所有 releases"""
        endpoint = f"/repos/{self.owner}/{self.repo}/releases"
        result = await self._request("GET", endpoint)
        return result if result else []

    async def get_tags(self) -> list[dict[str, Any]]:
        """获取所有 tags"""
        endpoint = f"/repos/{self.owner}/{self.repo}/tags"
        result = await self._request("GET", endpoint)
        return result if result else []


# Singleton instance
_service_instance: GitHubAPIService | None = None


def get_github_api_service() -> GitHubAPIService:
    """获取 GitHub API 服务单例"""
    global _service_instance
    if _service_instance is None:
        _service_instance = GitHubAPIService()
    return _service_instance
