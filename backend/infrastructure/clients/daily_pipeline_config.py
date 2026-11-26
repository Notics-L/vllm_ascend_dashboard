"""Read the configured Nightly catalog from GitHub."""
import json

import httpx

from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.github_credentials import GitHubCredential


async def read_nightly_config(repository: str, branch: str, path: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.get(f"https://raw.githubusercontent.com/{repository}/{branch}/{path}")
        response.raise_for_status()
        return response.text


async def check_trigger_permission(repository: str, credential: GitHubCredential) -> tuple[bool, str]:
    headers = {"Accept": "application/vnd.github+json"}
    async with GitHubAsyncClient(
        audit_source="daily_config", credential=credential, headers=headers, timeout=10,
    ) as client:
        repo = await client.get(f"/repos/{repository}")
        repo.raise_for_status()
        workflow = await client.get(f"/repos/{repository}/actions/workflows/schedule_daily_test.yaml")
        workflow.raise_for_status()

    if workflow.json().get("state") != "active":
        return False, "目标工作流未启用"
    push = repo.json().get("permissions", {}).get("push")
    if push is False:
        return False, "当前账号没有目标仓库的写入权限"
    scopes = repo.headers.get("X-OAuth-Scopes")
    if scopes is None or credential.fine_grained:
        return False, "工作流可访问，无法通过只读查询确认 Token 的 Actions 写权限"
    if "repo" not in {scope.strip() for scope in scopes.split(",")}:
        return False, "Token 缺少触发工作流所需的 repo 授权"
    if push is not True:
        return False, "无法确认当前账号对目标仓库的写入权限"
    return True, "已确认 Token 具备目标工作流的触发权限"


async def dispatch_daily_workflow(
    repository: str, branch: str, credential: GitHubCredential, cases: dict[str, list[str]],
) -> int:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    async with GitHubAsyncClient(
        audit_source="daily_config", credential=credential, headers=headers, timeout=30,
    ) as client:
        response = await client.post(
            f"/repos/{repository}/actions/workflows/schedule_daily_test.yaml/dispatches",
            json={
                "ref": branch,
                "inputs": {
                    "vllm_ascend_branch": branch,
                    "daily_test_cases": json.dumps(cases, ensure_ascii=False),
                },
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
            run_id = payload.get("workflow_run_id") if isinstance(payload, dict) else None
        except ValueError as exc:
            raise ValueError("GitHub 触发响应缺少 Run ID，请检查 GitHub 运行记录") from exc
        if type(run_id) is not int or run_id <= 0:
            raise ValueError("GitHub 触发响应缺少 Run ID，请检查 GitHub 运行记录")
        return run_id
