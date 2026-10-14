"""
贡献者数据采集模块

从 GitHub 拉取 PR/Commit/Review 数据，执行分类分析，并增量写入数据库。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contributor_board.classifier import ClassificationResult, classify_commit
from contributor_board.role_analytics import detect_roles
from infrastructure.clients.github_client import (
    GitHubAPIError,
    GitHubClient,
    GitHubRateLimitError,
)
from infrastructure.core.config import settings
from infrastructure.db.base import SessionLocal as AsyncSessionLocal
from infrastructure.persistence.models import (
    CommitClassification,
    Contributor,
    ContributorMetrics,
    ErrorRecord,
    ImageBuildPlan,
)

logger = logging.getLogger(__name__)

# "Revert "某些原始 commit message""  — capture group 1 is the original message
_REVERT_MESSAGE_RE = re.compile(r'^Revert\s+"(.+)"', re.DOTALL)

# 从 email 或 bio 中提取内部工号 (如 Z0012345 / 30012345)
_EMPLOYEE_ID_RE = re.compile(r"\bZ?\d{7,8}\b")

# Batch commit interval (number of DB operations between commits)
_COMMIT_BATCH_SIZE = 20


@dataclass
class SyncResult:
    """同步结果数据类"""

    contributors_synced: int = 0
    commits_classified: int = 0
    build_plans_created: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0
    synced_at: datetime | None = None


class ContributorSyncCollector:
    """贡献者同步采集器

    负责：
    1. 拉取时间范围内的 PR 列表
    2. 对每个 PR 拉取 commits / reviews / files
    3. 更新 Contributor 主表
    4. 对 commit 进行分类并写入 CommitClassification
    5. 检测 revert commit 并记录到 ErrorRecord
    6. 聚合每个 contributor 的指标到 ContributorMetrics
    """

    def __init__(self, github_client: GitHubClient, db_session: AsyncSession):
        self.github = github_client
        self.db = db_session
        self.owner = github_client.owner
        self.repo = github_client.repo

    # ── 入口方法 ──────────────────────────────────────────────────

    async def collect(
        self,
        since: datetime | None = None,
    ) -> SyncResult:
        start_time = datetime.now(UTC)
        result = SyncResult()

        if since is None:
            since = datetime(2024, 1, 1, tzinfo=UTC)

        logger.info(
            "Starting contributor sync: %s/%s since=%s",
            self.owner,
            self.repo,
            since.isoformat(),
        )

        try:
            prs = await self._fetch_prs(since)
            logger.info("Fetched %d PRs for contributor sync", len(prs))

            # 每个贡献者在当前采集周期内的临时指标聚合
            contributor_aggregates: dict[str, dict[str, Any]] = {}

            for pr in prs:
                pr_number = pr.get("number")
                if pr_number is None:
                    continue

                try:
                    await self._process_pr(pr, pr_number, result, contributor_aggregates)
                except GitHubRateLimitError as e:
                    logger.error("Rate limit exceeded at PR #%s, stopping sync: %s", pr_number, e)
                    result.errors.append(f"Rate limit at PR #{pr_number}: {e}")
                    break
                except GitHubAPIError as e:
                    logger.error("API error processing PR #%s: %s", pr_number, e)
                    result.errors.append(f"API error PR #{pr_number}: {e}")
                    continue
                except Exception as e:
                    logger.error(
                        "Unexpected error processing PR #%s: %s", pr_number, e, exc_info=True
                    )
                    result.errors.append(f"Unexpected error PR #{pr_number}: {e}")
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass
                    continue

            # 聚合写入 ContributorMetrics
            await self._upsert_metrics(contributor_aggregates, since, result)

        except Exception as e:
            logger.error("Contributor sync failed: %s", e, exc_info=True)
            result.errors.append(f"Sync failed: {e}")

        end_time = datetime.now(UTC)
        result.duration_seconds = (end_time - start_time).total_seconds()
        result.synced_at = end_time

        logger.info(
            "Contributor sync finished: contributors=%d commits=%d errors=%d duration=%.1fs",
            result.contributors_synced,
            result.commits_classified,
            len(result.errors),
            result.duration_seconds,
        )

        return result

    # ── 私有方法：PR 拉取 ─────────────────────────────────────────

    async def _fetch_prs(self, since: datetime) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        try:
            return await self.github.get_pull_requests_by_updated_range(
                self.owner,
                self.repo,
                since,
                now,
                max_items=None,
            )
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.error("Failed to fetch PRs since %s: %s", since.isoformat(), e)
            return []


def _resolve_repos() -> list[tuple[str, str]]:
    """解析需要采集的仓库列表。

    优先级:
    1. CONTRIBUTOR_REPOS 环境变量 (格式: owner/repo,owner/repo,...)
    2. GITHUB_OWNER / GITHUB_REPO 默认值
    """
    repos_cfg = settings.CONTRIBUTOR_REPOS.strip()
    if repos_cfg:
        parsed: list[tuple[str, str]] = []
        for entry in repos_cfg.split(","):
            entry = entry.strip()
            if "/" not in entry:
                continue
            parts = entry.split("/", 1)
            parsed.append((parts[0].strip(), parts[1].strip()))
        if parsed:
            return parsed

    return [(settings.GITHUB_OWNER, settings.GITHUB_REPO)]


# ── 模块级入口函数 ──────────────────────────────────────────────

async def sync_contributors(since_date: datetime | None = None) -> SyncResult:
    """执行贡献者数据同步的主入口函数（支持多仓库）

    Args:
        since_date: 增量同步的起始时间，默认为 7 天前

    Returns:
        SyncResult: 多仓库合并后的同步结果统计
    """
    token = settings.GITHUB_TOKEN
    if not token:
        logger.warning("GITHUB_TOKEN is not configured, skipping contributor sync")
        return SyncResult(errors=["GITHUB_TOKEN not configured"])

    repos = _resolve_repos()
    logger.info("Contributor sync target repos: %s", repos)

    combined = SyncResult()

    for owner, repo in repos:
        repo_key = f"{owner}/{repo}"
        logger.info("=== Starting sync for repo: %s ===", repo_key)
        try:
            async with GitHubClient(token=token, owner=owner, repo=repo) as github_client:
                async with AsyncSessionLocal() as db:
                    collector = ContributorSyncCollector(github_client, db)
                    result = await collector.collect(since=since_date)

            combined.contributors_synced += result.contributors_synced
            combined.commits_classified += result.commits_classified
            combined.build_plans_created += result.build_plans_created
            combined.errors.extend(result.errors)
        except Exception as e:
            logger.error("Sync failed for repo %s: %s", repo_key, e, exc_info=True)
            combined.errors.append(f"Repo {repo_key}: {e}")

    if combined.synced_at is None:
        combined.synced_at = datetime.now(UTC)

    logger.info(
        "All repos sync finished: total_contributors=%d total_commits=%d total_build_plans=%d total_errors=%d",
        combined.contributors_synced,
        combined.commits_classified,
        combined.build_plans_created,
        len(combined.errors),
    )
    return combined
