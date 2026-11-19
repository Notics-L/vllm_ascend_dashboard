"""Create a real revert PR through GitHub, without cloning or merging the base."""

import httpx

from infrastructure.clients.daily_pipeline_collection import DailyCollectionClient

REVERT_MUTATION = """
mutation DailyRevert($input: RevertPullRequestInput!) {
  revertPullRequest(input: $input) {
    revertPullRequest { number url state merged }
  }
}
"""


class RevertError(Exception):
    """Actionable, credential-free repository-write failure."""


class RetryableRevertError(RevertError):
    """Transport/rate-limit errors can be retried after checking for an existing PR."""


class DailyRevertClient(DailyCollectionClient):
    async def _json(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise RetryableRevertError("GitHub 连接失败，稍后重试并检查是否已创建回退 PR") from None
        if (
            response.status_code == 429
            or response.status_code >= 500
            or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
            )
        ):
            raise RetryableRevertError("GitHub 暂时不可用或触发限流，稍后重试")
        if response.status_code in (401, 403):
            raise RevertError("GitHub Token 没有目标仓库的回退 PR 创建权限")
        if not response.is_success:
            raise RevertError(f"GitHub 回退请求失败（HTTP {response.status_code}）")
        try:
            return response.json()
        except ValueError:
            raise RetryableRevertError("GitHub 返回无效数据，稍后确认回退结果") from None

    async def _existing(self, original, marker):
        # GitHub's native revert action uses this deterministic head branch.
        # Exact head/base filters avoid scanning the repository's complete history.
        params = {
            "state": "all",
            "head": f"{self.repository.split('/')[0]}:revert-{original['number']}-{original['head']['ref']}",
            "base": original["base"]["ref"],
            "per_page": 100,
        }
        rows = await self._json("GET", f"{self.prefix}/pulls", params=params)
        if not isinstance(rows, list):
            raise RetryableRevertError("GitHub 回退 PR 查询结果无效")
        for pr in rows:
            if marker in (pr.get("body") or ""):
                if pr["state"] == "closed" and not pr.get("merged_at"):
                    raise RevertError("已有回退 PR 已关闭且未合入，请人工处理")
                return {
                    "number": pr["number"],
                    "url": pr["html_url"],
                    "state": pr["state"],
                    "merged": bool(pr.get("merged_at")),
                }
        return None

    async def create_or_reuse(self, number, run_url):
        original = await self._json("GET", f"{self.prefix}/pulls/{number}")
        if not original.get("merged_at") or not original.get("node_id"):
            raise RevertError("定位的问题 PR 尚未合入，不能创建回退 PR")
        if original.get("changed_files") == 0:
            raise RevertError(
                f"定位的 PR #{number} 没有文件改动（空提交），无法创建回退 PR，请核对定位结果"
            )
        marker = f"<!-- daily-pipeline-revert:{self.repository}#{number} -->"
        existing = await self._existing(original, marker)
        if existing:
            return existing
        variables = {
            "input": {
                "pullRequestId": original["node_id"],
                "title": f"Revert #{number}: {original['title']}"[:255],
                "body": f"{marker}\n\nReverts https://github.com/{self.repository}/pull/{number}\n\nDaily run: {run_url}",
                "draft": False,
            }
        }
        payload = await self._json(
            "POST", "/graphql", json={"query": REVERT_MUTATION, "variables": variables}
        )
        if payload.get("errors"):
            # A previous POST may have succeeded even if its response was lost,
            # or a concurrent observer may have requested the same revert.
            existing = await self._existing(original, marker)
            if existing:
                return existing
            kinds = {error.get("type") for error in payload["errors"]}
            if kinds & {"RATE_LIMITED", "INTERNAL", "SERVICE_UNAVAILABLE"}:
                raise RetryableRevertError("GitHub 回退服务暂时不可用，稍后重试")
            if "FORBIDDEN" in kinds:
                raise RevertError("GitHub Token 没有目标仓库的回退 PR 创建权限")
            if any(
                str(error.get("message", "")).strip().lower() == "not revertable"
                for error in payload["errors"]
            ):
                raise RevertError(
                    f"GitHub 判定原 PR #{number} 无法自动回退（Not revertable），请检查回退冲突及分支历史"
                )
            raise RevertError("GitHub 无法自动创建回退 PR，请检查回退冲突及仓库规则")
        result = ((payload.get("data") or {}).get("revertPullRequest") or {}).get(
            "revertPullRequest"
        )
        if not isinstance(result, dict) or not result.get("number") or not result.get("url"):
            raise RetryableRevertError("GitHub 尚未返回回退 PR，稍后确认创建结果")
        return result
