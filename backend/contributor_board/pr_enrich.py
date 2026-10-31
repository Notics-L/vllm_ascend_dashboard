"""
从 pull_requests 表直接聚合贡献者信息（只读，不写贡献者表）。

复用 PR 流水线的 detect_company、邮箱提取等能力，
为贡献者分析的展示层补充公司/邮箱/机器人标识/PR效率指标。
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import PullRequest
from tooling.company_detector import detect_company

logger = logging.getLogger(__name__)


def _is_bot(login: str) -> bool:
    return login.lower().endswith("[bot]") or login.lower().endswith("-bot")


def _extract_emails_from_data(data: dict | None) -> list[str]:
    """从 PR data JSON 的 commits 中提取 author emails"""
    if not data or not isinstance(data, dict):
        return []
    commits = data.get("commits")
    if not isinstance(commits, list):
        return []
    emails = []
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        commit_data = commit.get("commit")
        if not isinstance(commit_data, dict):
            continue
        author = commit_data.get("author")
        if not isinstance(author, dict):
            continue
        email = author.get("email")
        if email:
            emails.append(email)
    return emails


async def get_pr_stats_for_logins(
    db: AsyncSession,
    logins: list[str],
    owner: str | None = None,
    repo: str | None = None,
    days: int = 365,
) -> dict[str, dict[str, Any]]:
    """
    从 pull_requests 表为指定 logins 聚合 PR 统计信息。

    Returns:
        {login: {
            emails: list[str],
            primary_email: str | None,
            company: str | None,
            is_bot: bool,
            pr_count: int,
            merged_count: int,
            lines_added: int,
            lines_removed: int,
            ci_failure_count: int,
            first_review_hours_avg: float | None,
            merge_hours_avg: float | None,
            ci_duration_hours_avg: float | None,
        }}
    """
    if not logins:
        return {}

    from datetime import UTC, datetime

    since = datetime.now(UTC) - timedelta(days=days)

    stmt = select(
        PullRequest.author,
        PullRequest.author_email,
        PullRequest.author_avatar_url,
        PullRequest.state,
        PullRequest.additions,
        PullRequest.deletions,
        PullRequest.ci_status,
        PullRequest.created_at,
        PullRequest.first_review_at,
        PullRequest.merged_at,
        PullRequest.ci_started_at,
        PullRequest.ci_completed_at,
        PullRequest.data,
    ).where(
        PullRequest.author.in_(logins),
        PullRequest.created_at >= since,
    )
    if owner and repo:
        stmt = stmt.where(PullRequest.owner == owner, PullRequest.repo == repo)

    result = await db.execute(stmt)
    rows = result.all()

    stats: dict[str, dict[str, Any]] = {}
    for login in logins:
        stats[login] = {
            "emails": Counter(),
            "pr_count": 0,
            "merged_count": 0,
            "lines_added": 0,
            "lines_removed": 0,
            "ci_failure_count": 0,
            "first_review_durations": [],
            "merge_durations": [],
            "ci_durations": [],
        }

    for row in rows:
        login = row[0]
        s = stats.get(login)
        if not s:
            continue

        s["pr_count"] += 1
        s["lines_added"] += row[4] or 0
        s["lines_removed"] += row[5] or 0

        if row[3] == "merged":
            s["merged_count"] += 1

        if row[6] in ("failure", "failed"):
            s["ci_failure_count"] += 1

        # Email from PR-level field
        if row[1]:
            s["emails"][row[1]] += 1

        # Emails from data JSON commits
        for email in _extract_emails_from_data(row[12]):
            s["emails"][email] += 1

        # Review response duration
        if row[8] and row[7]:
            dur_h = (row[8] - row[7]).total_seconds() / 3600
            if dur_h > 0:
                s["first_review_durations"].append(dur_h)

        # Merge duration
        if row[9] and row[7]:
            dur_h = (row[9] - row[7]).total_seconds() / 3600
            if dur_h > 0:
                s["merge_durations"].append(dur_h)

        # CI duration
        if row[10] and row[11]:
            dur_h = (row[11] - row[10]).total_seconds() / 3600
            if dur_h > 0:
                s["ci_durations"].append(dur_h)

    # Finalize
    for login, s in stats.items():
        email_list = [e for e, _ in s["emails"].most_common()]
        primary_email = email_list[0] if email_list else None
        s["emails"] = email_list
        s["primary_email"] = primary_email
        s["company"] = detect_company(primary_email)
        s["is_bot"] = _is_bot(login)
        s["first_review_hours_avg"] = (
            round(sum(s["first_review_durations"]) / len(s["first_review_durations"]), 2)
            if s["first_review_durations"] else None
        )
        s["merge_hours_avg"] = (
            round(sum(s["merge_durations"]) / len(s["merge_durations"]), 2)
            if s["merge_durations"] else None
        )
        s["ci_duration_hours_avg"] = (
            round(sum(s["ci_durations"]) / len(s["ci_durations"]), 2)
            if s["ci_durations"] else None
        )
        del s["first_review_durations"]
        del s["merge_durations"]
        del s["ci_durations"]

    return stats


async def enrich_ranking_with_pr_data(
    db: AsyncSession,
    logins: list[str],
    repo_name: str | None = None,
    days: int = 365,
) -> dict[str, dict[str, Any]]:
    """
    为排行榜/四象限数据补充 PR 流水线的公司/邮箱/机器人信息。

    Args:
        db: 数据库会话
        logins: 贡献者 login 列表
        repo_name: 可选，指定仓库（如 "vllm-project/vllm-ascend"）
        days: 往前看多少天

    Returns:
        pr_enrich 结果的子集（只含展示字段）
    """
    owner = None
    repo = None
    if repo_name and "/" in repo_name:
        owner, _, repo = repo_name.partition("/")

    raw = await get_pr_stats_for_logins(db, logins, owner=owner, repo=repo, days=days)

    # 精简为展示所需字段
    enriched: dict[str, dict[str, Any]] = {}
    for login, s in raw.items():
        enriched[login] = {
            "company": s["company"],
            "primary_email": s["primary_email"],
            "emails": s["emails"],
            "is_bot": s["is_bot"],
            "pr_count": s["pr_count"],
            "merged_count": s["merged_count"],
            "ci_failure_count": s["ci_failure_count"],
            "first_review_hours_avg": s["first_review_hours_avg"],
            "merge_hours_avg": s["merge_hours_avg"],
            "ci_duration_hours_avg": s["ci_duration_hours_avg"],
        }
    return enriched


async def get_full_contributor_metrics_from_prs(
    db: AsyncSession,
    repo_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    从 pull_requests 表聚合全量贡献者指标（替代 _build_aggregated_metrics）。

    返回与 _build_aggregated_metrics 相同格式的 dict 列表，每项包含:
    login, avatar_url, contribution_score, error_score,
    merged_pr_count, pending_pr_count, closed_pr_count,
    code_additions, code_deletions, review_count, approval_count,
    commit_count, company, primary_email, is_bot, pr_efficiency。

    contribution_score 和 error_score 由 scoring_engine 实时计算。
    """
    from datetime import UTC, datetime

    from contributor_board.scoring_engine import (
        calculate_contribution_score,
        get_default_weights,
    )
    from infrastructure.persistence.models import ContributorBoardSettings

    # 1) 获取权重配置
    settings_row = (await db.execute(
        select(ContributorBoardSettings).where(
            ContributorBoardSettings.setting_key == "scoring_weights"
        )
    )).scalars().first()
    weights = get_default_weights()
    if settings_row and isinstance(settings_row.setting_value, dict):
        weights.update(settings_row.setting_value)

    # 2) 加载所有 PR
    stmt = select(
        PullRequest.author,
        PullRequest.author_avatar_url,
        PullRequest.author_email,
        PullRequest.state,
        PullRequest.additions,
        PullRequest.deletions,
        PullRequest.created_at,
        PullRequest.merged_at,
        PullRequest.first_review_at,
        PullRequest.ci_status,
        PullRequest.reviewers,
    )
    if repo_name and "/" in repo_name:
        owner, _, repo = repo_name.partition("/")
        stmt = stmt.where(PullRequest.owner == owner, PullRequest.repo == repo)
    elif repo_name:
        stmt = stmt.where(PullRequest.repo == repo_name)

    prs = (await db.execute(stmt)).all()

    # 3) 按 author 聚合
    agg: dict[str, dict[str, Any]] = {}
    for pr in prs:
        login = pr[0]
        if not login:
            continue
        if login not in agg:
            agg[login] = {
                "avatar_url": pr[1],
                "email": pr[2],
                "pr_count": 0,
                "merged_pr_count": 0,
                "pending_pr_count": 0,
                "closed_pr_count": 0,
                "code_additions": 0,
                "code_deletions": 0,
                "review_count": 0,
                "approval_count": 0,
                "ci_failure_count": 0,
                "first_review_durations": [],
                "merge_durations": [],
            }
        a = agg[login]
        # 保留最近的 avatar/email
        if pr[1]:
            a["avatar_url"] = pr[1]
        if pr[2]:
            a["email"] = pr[2]

        a["pr_count"] += 1
        a["code_additions"] += pr[4] or 0
        a["code_deletions"] += pr[5] or 0

        if pr[3] == "merged":
            a["merged_pr_count"] += 1
            if pr[6] and pr[7]:
                dur_h = (pr[7] - pr[6]).total_seconds() / 3600
                if dur_h > 0:
                    a["merge_durations"].append(dur_h)
        elif pr[3] == "open":
            a["pending_pr_count"] += 1
        elif pr[3] == "closed":
            a["closed_pr_count"] += 1

        if pr[9] in ("failure", "failed"):
            a["ci_failure_count"] += 1

        if pr[8] and pr[6]:
            dur_h = (pr[8] - pr[6]).total_seconds() / 3600
            if dur_h > 0:
                a["first_review_durations"].append(dur_h)

        # Reviewer 维度
        reviewers = pr[10] if isinstance(pr[10], list) else []
        for rv in reviewers:
            if isinstance(rv, str):
                rv_login = rv
                rv_approved = False
            elif isinstance(rv, dict):
                rv_login = rv.get("login") or ""
                rv_approved = rv.get("state") in ("APPROVED", "approved")
            else:
                continue
            if not rv_login or rv_login == login:
                continue
            if rv_login not in agg:
                agg[rv_login] = {
                    "avatar_url": None,
                    "email": None,
                    "pr_count": 0,
                    "merged_pr_count": 0,
                    "pending_pr_count": 0,
                    "closed_pr_count": 0,
                    "code_additions": 0,
                    "code_deletions": 0,
                    "review_count": 0,
                    "approval_count": 0,
                    "ci_failure_count": 0,
                    "first_review_durations": [],
                    "merge_durations": [],
                }
            r = agg[rv_login]
            r["review_count"] += 1
            if rv_approved:
                r["approval_count"] += 1

    # 4) 计算分数 + 构建结果
    result: list[dict[str, Any]] = []
    for login, a in agg.items():
        error_score = float(a["ci_failure_count"]) * 0.1

        metrics = {
            "merged_pr_count": a["merged_pr_count"],
            "code_additions": a["code_additions"],
            "code_deletions": a["code_deletions"],
            "review_count": a["review_count"],
            "approval_count": a["approval_count"],
            "error_score": error_score,
        }
        score = calculate_contribution_score(metrics, weights, commit_type_counts={})

        result.append({
            "login": login,
            "avatar_url": a["avatar_url"],
            "employee_id": None,
            "contribution_score": score,
            "error_score": round(error_score, 2),
            "merged_pr_count": a["merged_pr_count"],
            "pending_pr_count": a["pending_pr_count"],
            "code_additions": a["code_additions"],
            "code_deletions": a["code_deletions"],
            "review_count": a["review_count"],
            "approval_count": a["approval_count"],
            "commit_count": a["pr_count"],
            "role_label": None,
            "is_community": False,
            "company": detect_company(a["email"]),
            "primary_email": a["email"],
            "is_bot": _is_bot(login),
            "first_review_hours_avg": (
                round(sum(a["first_review_durations"]) / len(a["first_review_durations"]), 2)
                if a["first_review_durations"] else None
            ),
            "merge_hours_avg": (
                round(sum(a["merge_durations"]) / len(a["merge_durations"]), 2)
                if a["merge_durations"] else None
            ),
            "ci_failure_count": a["ci_failure_count"],
            "pr_count": a["pr_count"],
        })

    logger.debug(
        "get_full_contributor_metrics_from_prs: %d contributors from %d PRs",
        len(result), len(prs),
    )
    return result
