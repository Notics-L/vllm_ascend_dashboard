"""Normalize the exact diagnosed PR before scheduling a repository write."""

import re
from urllib.parse import urlparse

REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
PR_PATH_PATTERN = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?")


def located_pull_request(result, default_repository=None):
    if not isinstance(result, dict):
        raise ValueError("定位结果的 PR 信息无效")
    url = result.get("pr_url")
    if url and not isinstance(url, str):
        raise ValueError("请填写完整的 GitHub 问题 PR 链接")
    if url:
        parsed = urlparse(url)
        match = PR_PATH_PATTERN.fullmatch(parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not match:
            raise ValueError("请填写完整的 GitHub 问题 PR 链接")
        repository, number = f"{match[1]}/{match[2]}", int(match[3])
        if result.get("pr_repository") not in (None, repository) or result.get("pr_number") not in (
            None,
            number,
        ):
            raise ValueError("问题 PR 的链接、仓库和编号不一致")
    else:
        repository, number = (
            result.get("pr_repository") or default_repository,
            result.get("pr_number"),
        )
        if (
            not isinstance(repository, str)
            or not REPOSITORY_PATTERN.fullmatch(repository)
            or type(number) is not int
            or number <= 0
        ):
            raise ValueError("请检查全局仓库配置和定位结果中的 PR 编号")
    if default_repository and repository.lower() != default_repository.lower():
        raise ValueError("问题 PR 仓库与 Daily 全局仓库配置不一致")
    return {
        "pr_repository": repository,
        "pr_number": number,
        "pr_url": f"https://github.com/{repository}/pull/{number}",
    }
