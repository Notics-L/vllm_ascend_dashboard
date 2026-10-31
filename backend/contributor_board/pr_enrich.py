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

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import PullRequest
from tooling.company_detector import detect_company

logger = logging.getLogger(__name__)


def parse_repo_name(repo_name: str | None) -> tuple[str, str]:
    """解析 repo_name（owner/repo）为 (owner, repo)，缺省回退到全局配置。"""
    from infrastructure.core.config import settings

    owner = (settings.GITHUB_OWNER or "").strip()
    repo = (settings.GITHUB_REPO or "").strip()
    if repo_name and "/" in repo_name:
        o, _, r = repo_name.partition("/")
        return o, r
    return owner, repo


def _is_bot(login: str) -> bool:
    return login.lower().endswith("[bot]") or login.lower().endswith("-bot")


# code_line_weight 合理上界：代码行数是原始量纲（单贡献者可达数十万行），
# 该维度权重必须保持极低，否则分会被代码量淹没（历史 bug 曾出现 0.5 导致 70 万+ 分）。
MAX_CODE_LINE_WEIGHT = 0.01


def normalize_weights(weights: dict[str, Any]) -> dict[str, Any]:
    """规范化权重：强制 code_line_weight 不超过合理上界，防止历史脏数据/异常权重
    导致分数数量级失真。其余维度原样保留。"""
    w = dict(weights)
    try:
        clw = float(w.get("code_line_weight", 0))
    except (TypeError, ValueError):
        clw = 0.0
    if clw > MAX_CODE_LINE_WEIGHT:
        w["code_line_weight"] = MAX_CODE_LINE_WEIGHT
    return w


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
    days: int | None = None,
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
    weights = normalize_weights(weights)

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

    if days:
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = stmt.where(PullRequest.created_at >= since)

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


async def get_pr_based_ranking(
    db: AsyncSession,
    repo_name: str | None = None,
    days: int = 30,
    type: str | None = None,
    sort_by: str = "pr_count",
    skip: int = 0,
    limit: int = 20,
    company: str | None = None,
) -> dict[str, Any]:
    """完全复用 PR 流水线 get_contributors 聚合逻辑，保证数字 100% 一致。

    基础统计字段（pr_count / merged_count / lines_added / lines_removed /
    review_count / avg_first_response_hours / company / primary_email /
    author_count / reviewer_count）**直接取自 PR 流水线的 author_data.total /
    reviewer_data.total 与 items**，不再二次计算——这是之前排行榜人数
    （40/20）和 PR 流水线（420/240）对不上的根因修复。

    算分字段（contribution_score / error_score / dynamic_quadrant）基于
    PR 流水线的 items 字段通过 scoring_engine 计算，与四象限看板
    共用同一权重体系。

    Returns:
        {
            "author_count": int,       # PR 流水线 author 总数
            "reviewer_count": int,     # PR 流水线 reviewer 总数
            "total": int,              # author_count + reviewer_count
            "contributor_map": {login: {...score fields...}},
            "items": [PRPipelineContributor, ...],
        }
    """
    from datetime import UTC, datetime

    from sqlalchemy import case, func, select

    from pr_pipeline.pr_pipeline_service import PRPipelineService
    from contracts.schemas.pr_pipeline import PRPipelineContributor

    owner, repo = parse_repo_name(repo_name)
    svc = PRPipelineService()

    # 1) 拉 PR 流水线全量 contributor（author + reviewer），分页交给 PR 流水线
    #    一次拉够 100000 避免分页丢失，但 total 字段才是真实总数
    FETCH_CAP = 100000
    try:
        author_data = await svc.get_contributors(
            db, owner, repo, days, "author", 0, FETCH_CAP,
        )
        reviewer_data = await svc.get_contributors(
            db, owner, repo, days, "reviewer", 0, FETCH_CAP,
        )
        author_items: list[PRPipelineContributor] = list(author_data.items)
        reviewer_items: list[PRPipelineContributor] = list(reviewer_data.items)
        author_total = int(author_data.total)
        reviewer_total = int(reviewer_data.total)
    except Exception as e:
        logger.warning("get_pr_based_ranking via PRPipelineService failed (%s), falling back", e)
        author_items, reviewer_items = await _get_pr_based_items_direct(db, owner, repo, days)
        author_total = len(author_items)
        reviewer_total = len(reviewer_items)

    all_items = author_items + reviewer_items

    # 2) 轻量级查 PR 表：只为拿到 PR 流水线 items 里没有的字段
    #    （pending_pr_count / ci_failure_count / approval_count）
    #    只拉 5 个字段，单次 SQL，性能远好于 get_full_contributor_metrics_from_prs
    since = datetime.now(UTC) - timedelta(days=days)
    logins = list({it.username for it in all_items if it.username})
    extra_map = await _fetch_pr_extra_fields(db, logins, owner, repo, since)

    # 3) 算分：基于 PR 流水线的 items 字段（与四象限看板共用 scoring_engine）
    contributor_map = await _build_contributor_score_map(
        db, all_items, extra_map, repo_name=repo_name,
    )

    # 4) 按 type 过滤 + 排序 + 分页
    if type == "author":
        show_items = author_items
    elif type == "reviewer":
        show_items = reviewer_items
    else:
        show_items = all_items

    sort_key_map = {
        "pr_count": lambda it: it.pr_count or 0,
        "merged_count": lambda it: it.merged_count or 0,
        "lines_added": lambda it: it.lines_added or 0,
        "lines_removed": lambda it: it.lines_removed or 0,
        "review_count": lambda it: it.review_count or 0,
        "contribution_score": lambda it: contributor_map.get(it.username, {}).get("contribution_score", 0),
        "score": lambda it: contributor_map.get(it.username, {}).get("contribution_score", 0),
    }
    sort_fn = sort_key_map.get(sort_by, lambda it: it.pr_count or 0)
    show_items = sorted(show_items, key=sort_fn, reverse=True)

    paginated = show_items[skip:skip + limit]

    return {
        "author_count": author_total,
        "reviewer_count": reviewer_total,
        "total": author_total + reviewer_total,
        "contributor_map": contributor_map,
        "items": paginated,
    }


async def _fetch_pr_extra_fields(
    db: AsyncSession,
    logins: list[str],
    owner: str,
    repo: str,
    since: datetime,
) -> dict[str, dict[str, Any]]:
    """轻量级查 PR 表，只拉排行榜 items 里没有的 3 个字段。

    一次 SQL，按 author 聚合 pending_pr_count / ci_failure_count，
    另起一次 SQL 拉 approval_count（reviewer 维度）。
    """
    if not logins:
        return {}

    from sqlalchemy import case, func, select

    # Author 维度：pending / ci_failure
    author_stmt = (
        select(
            PullRequest.author,
            func.count(PullRequest.id).label("total_pr"),
            func.sum(case((PullRequest.state == "open", 1), else_=0)).label("pending"),
            func.sum(case((PullRequest.ci_status.in_(["failure", "failed"]), 1), else_=0)).label("ci_failure"),
        )
        .where(PullRequest.author.in_(logins))
        .where(PullRequest.owner == owner, PullRequest.repo == repo)
        .where(PullRequest.created_at >= since)
        .group_by(PullRequest.author)
    )
    extra: dict[str, dict[str, Any]] = {}
    for r in (await db.execute(author_stmt)).all():
        if not r[0]:
            continue
        extra[r[0]] = {
            "pending_pr_count": int(r[2] or 0),
            "ci_failure_count": int(r[3] or 0),
        }

    # Reviewer 维度：approval_count（reviewers JSON 里 state == APPROVED）
    #    用 Python 层遍历（与 PR 流水线 get_contributors 的 reviewer 聚合逻辑一致）
    reviewer_stmt = (
        select(PullRequest.reviewers)
        .where(PullRequest.owner == owner, PullRequest.repo == repo)
        .where(PullRequest.created_at >= since)
        .where(PullRequest.reviewers.isnot(None))
    )
    approval_map: dict[str, int] = {}
    for (r,) in (await db.execute(reviewer_stmt)).all():
        if not r or not isinstance(r, list):
            continue
        for rv in r:
            if not rv:
                continue
            if isinstance(rv, str):
                continue  # str 没有 state
            if not isinstance(rv, dict):
                continue
            login = rv.get("login") or ""
            if not login:
                continue
            if rv.get("state") in ("APPROVED", "approved"):
                approval_map[login] = approval_map.get(login, 0) + 1

    for login in logins:
        e = extra.setdefault(login, {"pending_pr_count": 0, "ci_failure_count": 0})
        e["approval_count"] = approval_map.get(login, 0)

    return extra


async def _build_contributor_score_map(
    db: AsyncSession,
    items: list["PRPipelineContributor"],
    extra_map: dict[str, dict[str, Any]],
    repo_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """基于 PR 流水线的 items 字段 + 额外 PR 字段，计算算分字段。

    分数 = calculate_contribution_score() + 四象限。
    基础指标（merged_pr_count / code_additions / review_count / approval_count）
    全部取自 PR 流水线的 items，与四象限看板一致。
    """
    if not items:
        return {}

    from contributor_board.quadrant_engine import calculate_quadrants
    from contributor_board.scoring_engine import (
        calculate_contribution_score,
        get_default_weights,
    )
    from contracts.schemas.contributor_board import ScoringWeightsConfig  # noqa: F401
    from infrastructure.persistence.models import ContributorBoardSettings

    # 1) 权重
    settings_row = (await db.execute(
        select(ContributorBoardSettings).where(
            ContributorBoardSettings.setting_key == "scoring_weights"
        )
    )).scalars().first()
    weights = get_default_weights()
    if settings_row and isinstance(settings_row.setting_value, dict):
        weights.update(settings_row.setting_value)
    weights = normalize_weights(weights)

    # 2) 构建用于四象限的 metrics（与四象限看板 _build_aggregated_metrics 字段对齐）
    metrics: list[dict[str, Any]] = []
    for it in items:
        extra = extra_map.get(it.username, {})
        ci_failure = int(extra.get("ci_failure_count", 0))
        error_score = float(ci_failure) * 0.1
        metrics.append({
            "login": it.username,
            "avatar_url": it.avatar_url,
            "contribution_score": 0,  # 占位
            "error_score": error_score,
            "merged_pr_count": it.merged_count or 0,
            "code_additions": it.lines_added or 0,
            "code_deletions": it.lines_removed or 0,
            "review_count": it.review_count or 0,
            "approval_count": int(extra.get("approval_count", 0)),
            "is_community": False,
            "role_label": None,
        })

    # 3) 用 scoring_engine 算分
    for m in metrics:
        score = calculate_contribution_score(
            {
                "merged_pr_count": m["merged_pr_count"],
                "code_additions": m["code_additions"],
                "code_deletions": m["code_deletions"],
                "review_count": m["review_count"],
                "approval_count": m["approval_count"],
                "error_score": m["error_score"],
            },
            weights,
            commit_type_counts={},
        )
        m["contribution_score"] = score

    # 4) 四象限
    y_axis_type = await _get_quadrant_y_axis_from_settings(db)
    axis_map = {
        "error_score": "error_score",
        "review_count": "review_count",
        "merged_pr_count": "merged_pr_count",
        "code_additions": "code_additions",
    }
    y_key = axis_map.get(y_axis_type, "review_count")
    active_metrics = [m for m in metrics if m["contribution_score"] > 0]
    if active_metrics:
        points, _, _ = calculate_quadrants(active_metrics, y_axis_type=y_key)
        quadrant_map = {p.login: p.quadrant for p in points}
    else:
        quadrant_map = {}

    # 5) 组装结果
    result: dict[str, dict[str, Any]] = {}
    for it in items:
        extra = extra_map.get(it.username, {})
        m = next((x for x in metrics if x["login"] == it.username), None)
        if not m:
            continue
        result[it.username] = {
            "contribution_score": m["contribution_score"],
            "error_score": m["error_score"],
            "merged_pr_count": it.merged_count or 0,
            "pending_pr_count": int(extra.get("pending_pr_count", 0)),
            "code_additions": it.lines_added or 0,
            "code_deletions": it.lines_removed or 0,
            "review_count": it.review_count or 0,
            "approval_count": int(extra.get("approval_count", 0)),
            "dynamic_quadrant": quadrant_map.get(it.username),
        }
    return result


async def _get_quadrant_y_axis_from_settings(db: AsyncSession) -> str:
    """从 ContributorBoardSettings 读四象限 Y 轴，默认 review_count。"""
    from infrastructure.persistence.models import ContributorBoardSettings

    row = (await db.execute(
        select(ContributorBoardSettings.setting_value).where(
            ContributorBoardSettings.setting_key == "quadrant_y_axis"
        )
    )).scalar_one_or_none()
    if row and isinstance(row, dict):
        return row.get("y_axis_type", "review_count")
    return "review_count"


async def _get_pr_based_items_direct(
    db: AsyncSession,
    owner: str,
    repo: str,
    days: int,
) -> tuple[list["PRPipelineContributor"], list["PRPipelineContributor"]]:
    """降级实现：直接用 pull_requests 表聚合 author 和 reviewer 列表。"""
    from datetime import UTC, datetime
    from sqlalchemy import select, func
    from contracts.schemas.pr_pipeline import PRPipelineContributor

    since = datetime.now(UTC) - timedelta(days=days)

    # Author 聚合
    author_stmt = (
        select(
            PullRequest.author,
            func.max(PullRequest.author_avatar_url).label("avatar_url"),
            func.max(PullRequest.author_email).label("email"),
            func.count(PullRequest.id).label("pr_count"),
            func.sum(case((PullRequest.state == "merged", 1), else_=0)).label("merged_count"),
            func.sum(PullRequest.additions).label("lines_added"),
            func.sum(PullRequest.deletions).label("lines_removed"),
        )
        .where(PullRequest.owner == owner, PullRequest.repo == repo)
        .where(PullRequest.created_at >= since)
        .where(PullRequest.author.isnot(None))
        .group_by(PullRequest.author)
    )
    author_result = await db.execute(author_stmt)
    author_items: list[PRPipelineContributor] = []
    for r in author_result.all():
        if not r[0]:
            continue
        email = r[2]
        author_items.append(PRPipelineContributor(
            username=r[0],
            avatar_url=r[1],
            emails=[email] if email else [],
            primary_email=email,
            type="author",
            company=detect_company(email),
            pr_count=r[3] or 0,
            merged_count=r[4] or 0,
            lines_added=r[5] or 0,
            lines_removed=r[6] or 0,
        ))
    author_items.sort(key=lambda it: it.pr_count or 0, reverse=True)

    # Reviewer 聚合：遍历 reviewers JSON
    reviewer_stmt = (
        select(PullRequest.reviewers, PullRequest.first_review_at, PullRequest.created_at)
        .where(PullRequest.owner == owner, PullRequest.repo == repo)
        .where(PullRequest.created_at >= since)
        .where(PullRequest.reviewers.isnot(None))
    )
    reviewer_result = await db.execute(reviewer_stmt)
    reviewer_stats: dict[str, dict[str, Any]] = {}
    for r in reviewer_result.all():
        if not r[0] or not isinstance(r[0], list):
            continue
        for rv in r[0]:
            if not rv:
                continue
            if isinstance(rv, str):
                login = rv
            elif isinstance(rv, dict):
                login = rv.get("login") or ""
            else:
                continue
            if not login:
                continue
            if login not in reviewer_stats:
                reviewer_stats[login] = {"count": 0, "response_hours": []}
            reviewer_stats[login]["count"] += 1
            if r[1] and r[2]:
                hours = (r[1] - r[2]).total_seconds() / 3600
                reviewer_stats[login]["response_hours"].append(hours)

    reviewer_items: list[PRPipelineContributor] = []
    for login, stats in reviewer_stats.items():
        avg = (
            round(sum(stats["response_hours"]) / len(stats["response_hours"]), 1)
            if stats["response_hours"] else None
        )
        reviewer_items.append(PRPipelineContributor(
            username=login,
            type="reviewer",
            company=None,  # PR 流水线 reviewer 聚合的 company 来自 DB 反查，降级这里留空
            review_count=stats["count"],
            avg_first_response_hours=avg,
        ))
    reviewer_items.sort(key=lambda it: it.review_count or 0, reverse=True)

    return author_items, reviewer_items


async def get_pr_score_map(
    db: AsyncSession,
    logins: list[str],
    repo_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """为指定 logins 计算算分字段（复用全量 PR 聚合 + 四象限）。

    Returns:
        {login: {
            contribution_score, error_score,
            merged_pr_count, pending_pr_count,
            code_additions, code_deletions,
            review_count, approval_count,
            dynamic_quadrant,
        }}
    """
    if not logins:
        return {}

    all_metrics = await get_full_contributor_metrics_from_prs(db, repo_name=repo_name)
    if not all_metrics:
        return {
            login: {
                "contribution_score": 0.0, "error_score": 0.0,
                "merged_pr_count": 0, "pending_pr_count": 0,
                "code_additions": 0, "code_deletions": 0,
                "review_count": 0, "approval_count": 0,
                "dynamic_quadrant": None,
            }
            for login in logins
        }

    login_set = set(logins)
    present = {m["login"] for m in all_metrics}
    quadrants = await _compute_quadrants_for_logins(all_metrics, db)

    score_map: dict[str, dict[str, Any]] = {}
    for login in logins:
        m = next((x for x in all_metrics if x["login"] == login), None)
        if m:
            score_map[login] = {
                "contribution_score": m["contribution_score"],
                "error_score": m["error_score"],
                "merged_pr_count": m["merged_pr_count"],
                "pending_pr_count": m["pending_pr_count"],
                "code_additions": m["code_additions"],
                "code_deletions": m["code_deletions"],
                "review_count": m["review_count"],
                "approval_count": m["approval_count"],
                "dynamic_quadrant": quadrants.get(login),
            }
        else:
            score_map[login] = {
                "contribution_score": 0.0, "error_score": 0.0,
                "merged_pr_count": 0, "pending_pr_count": 0,
                "code_additions": 0, "code_deletions": 0,
                "review_count": 0, "approval_count": 0,
                "dynamic_quadrant": None,
            }
    _ = present
    _ = login_set
    return score_map


async def _compute_quadrants_for_logins(
    all_metrics: list[dict[str, Any]], db: AsyncSession
) -> dict[str, str]:
    """基于全局设置的 Y 轴类型计算四象限，返回 login → quadrant。"""
    from contributor_board.quadrant_engine import calculate_quadrants

    from infrastructure.persistence.models import ContributorBoardSettings

    from sqlalchemy import select as sa_select

    y_axis_type = "review_count"
    row = (await db.execute(
        sa_select(ContributorBoardSettings.setting_value).where(
            ContributorBoardSettings.setting_key == "quadrant_y_axis"
        )
    )).scalar_one_or_none()
    if row and isinstance(row, dict):
        y_axis_type = row.get("y_axis_type", "review_count")

    axis_map = {
        "error_score": "error_score",
        "review_count": "review_count",
        "merged_pr_count": "merged_pr_count",
        "code_additions": "code_additions",
    }
    y_key = axis_map.get(y_axis_type, "review_count")
    active = [m for m in all_metrics if m.get("contribution_score", 0) > 0]
    if active:
        points, _, _ = calculate_quadrants(active, y_axis_type=y_key)
        return {p.login: p.quadrant for p in points}
    return {}
