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
            since = datetime.now(UTC) - timedelta(days=7)

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

    # ── 私有方法：处理单个 PR ─────────────────────────────────────

    async def _process_pr(
        self,
        pr: dict[str, Any],
        pr_number: int,
        result: SyncResult,
        contributor_aggregates: dict[str, dict[str, Any]],
    ) -> None:
        """处理单个 PR，更新 contributor 并 classify commits."""
        user_info = pr.get("user") or {}
        github_id = user_info.get("id")
        login = user_info.get("login", "")
        if not login:
            return

        # 拉取 reviews / commits / files
        reviews: list[dict[str, Any]] = []
        commits: list[dict[str, Any]] = []

        try:
            reviews = await self.github.get_pr_reviews(self.owner, self.repo, pr_number)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch reviews for PR #%s: %s", pr_number, e)

        try:
            commits = await self.github.get_pr_commits(self.owner, self.repo, pr_number)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch commits for PR #%s: %s", pr_number, e)

        # 更新 Contributor 记录
        db_contributor = await self._upsert_contributor(user_info, commits, login)
        if db_contributor is None:
            return

        # 初始化/获取该贡献者聚合桶
        agg = contributor_aggregates.setdefault(login, {
            "contributor_id": db_contributor.id,
            "merged_pr_count": 0,
            "pending_pr_count": 0,
            "code_additions": 0,
            "code_deletions": 0,
            "review_count": 0,
            "approval_count": 0,
            "merge_count": 0,
            "core_module_count": 0,
            "closed_pr_count": 0,
            "error_score": 0.0,
        })

        # 更新贡献时间窗口
        updated_at = self._parse_datetime(pr.get("updated_at"))
        if updated_at:
            await self._update_contribution_dates(db_contributor, updated_at)

        # 判定 PR 状态
        is_merged = bool(pr.get("merged") or pr.get("merged_at"))
        pr_state = pr.get("state", "closed")

        if is_merged:
            agg["merged_pr_count"] += 1
            # 从 PR summary 获取 add / delete
            agg["code_additions"] += pr.get("additions") or 0
            agg["code_deletions"] += pr.get("deletions") or 0
        elif pr_state == "open":
            agg["pending_pr_count"] += 1
        else:
            agg["closed_pr_count"] += 1

        # 聚合 review 指标
        for review in reviews:
            review_user = (review.get("user") or {}).get("login", "")
            if review_user == login:
                agg["review_count"] += 1
                if review.get("state") == "APPROVED":
                    agg["approval_count"] += 1

        # 对 commits 进行分类
        for commit in commits:
            commit_sha = commit.get("sha", "")
            if not commit_sha:
                continue

            commit_info = commit.get("commit", {})
            commit_message = commit_info.get("message", "")
            files_changed = self._get_files_from_commit(commit)

            classification_result = classify_commit(commit_message, files_changed)
            await self._upsert_commit_classification(
                commit_sha,
                commit_message,
                classification_result,
                login,
                pr_number,
                len(files_changed) if files_changed else 0,
            )
            result.commits_classified += 1

            # 核心模块计数
            if classification_result.is_core_module:
                agg["core_module_count"] += 1

            # Revert 检测
            if classification_result.commit_type.value == "Revert":
                original_message = _REVERT_MESSAGE_RE.search(commit_message)
                description = original_message.group(1) if original_message else ""
                await self._record_revert_error(login, pr_number, commit_sha, description, result)
                agg["error_score"] += 1.0

        # 周期 commit 提交
        if result.commits_classified % _COMMIT_BATCH_SIZE == 0:
            await self.db.commit()
            logger.debug(
                "Committed contributor sync batch: %d commits classified",
                result.commits_classified,
            )

    # ── 私有方法：Contributor ─────────────────────────────────────

    @staticmethod
    def _extract_employee_id(user_info: dict[str, Any], email: str) -> str | None:
        """从 GitHub 用户信息中提取内部工号。

        优先从 email 匹配，其次从 bio 匹配，最后从 login/name 匹配。
        """
        search_targets = [
            email,
            user_info.get("email", ""),
            user_info.get("bio", ""),
            user_info.get("company", ""),
            user_info.get("name", ""),
            user_info.get("login", ""),
        ]
        for text in search_targets:
            if not text:
                continue
            matches = _EMPLOYEE_ID_RE.findall(text)
            if matches:
                return matches[0]
        return None

    async def _upsert_contributor(
        self,
        user_info: dict[str, Any],
        commits: list[dict[str, Any]],
        login: str,
    ) -> Contributor | None:
        github_id = user_info.get("id")
        if github_id is None:
            return None

        stmt = select(Contributor).where(Contributor.github_id == github_id)
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        name = user_info.get("name", "")
        avatar_url = user_info.get("avatar_url", "")
        email = ""
        commit_items = commits if isinstance(commits, list) else []
        if commit_items:
            for c in commit_items:
                commit_author_login = (c.get("author") or {}).get("login", "")
                if not commit_author_login or commit_author_login == login:
                    email = (c.get("commit", {}).get("author", {}).get("email", "")) or email
                    if email:
                        break

        employee_id = self._extract_employee_id(user_info, email)

        if existing:
            existing.login = login
            existing.avatar_url = avatar_url or existing.avatar_url
            existing.email = email or existing.email
            existing.name = name or existing.name
            if employee_id:
                existing.employee_id = employee_id
            existing.updated_at = datetime.now(UTC)
            logger.debug("Updated Contributor %s (id=%s)", login, github_id)
            return existing
        else:
            is_community = not bool(employee_id)
            company = user_info.get("company", "") or ""
            new_contributor = Contributor(
                github_id=github_id,
                login=login,
                avatar_url=avatar_url,
                email=email,
                name=name,
                is_community=is_community,
                role_types=[],
                company=company,
                employee_id=employee_id,
            )
            self.db.add(new_contributor)
            logger.debug("Created Contributor %s (id=%s, emp=%s)", login, github_id, employee_id)
            return new_contributor

    async def _update_contribution_dates(
        self,
        contributor: Contributor,
        updated_at: datetime,
    ) -> None:
        if contributor.first_contribution_at is None or updated_at < contributor.first_contribution_at:
            contributor.first_contribution_at = updated_at
        if contributor.last_contribution_at is None or updated_at > contributor.last_contribution_at:
            contributor.last_contribution_at = updated_at

    # ── 私有方法：Commit Classification ───────────────────────────

    async def _upsert_commit_classification(
        self,
        commit_sha: str,
        commit_message: str,
        classification: ClassificationResult,
        contributor_login: str,
        pr_number: int,
        files_changed_count: int,
    ) -> None:
        stmt = select(CommitClassification).where(CommitClassification.commit_sha == commit_sha)
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.commit_message = commit_message
            existing.commit_type = classification.commit_type.value
            existing.confidence = classification.confidence
            existing.modules_touched = classification.modules_touched
            existing.files_changed_count = files_changed_count
            existing.is_chip_specific = classification.is_chip_specific
            existing.chip_target = classification.chip_target
            existing.contributor_login = contributor_login
            existing.pr_number = pr_number
        else:
            new_entry = CommitClassification(
                commit_sha=commit_sha,
                commit_message=commit_message,
                commit_type=classification.commit_type.value,
                confidence=classification.confidence,
                modules_touched=classification.modules_touched,
                files_changed_count=files_changed_count,
                is_chip_specific=classification.is_chip_specific,
                chip_target=classification.chip_target,
                contributor_login=contributor_login,
                pr_number=pr_number,
            )
            self.db.add(new_entry)

    # ── 私有方法：Error Record / Revert ───────────────────────────

    async def _record_revert_error(
        self,
        contributor_login: str,
        pr_number: int,
        commit_sha: str,
        description: str,
        result: SyncResult,
    ) -> None:
        """记录 revert 错误到 ErrorRecord 表。"""
        stmt = select(ErrorRecord).where(
            and_(
                ErrorRecord.contributor_login == contributor_login,
                ErrorRecord.error_type == "revert",
                ErrorRecord.related_pr_number == pr_number,
            )
        )
        db_result = await self.db.execute(stmt)
        existing = db_result.scalar_one_or_none()

        if existing:
            existing.revert_count += 1
            existing.weight_score += 5.0
            if description:
                existing.description = f"{existing.description or ''}\n- {description}"
        else:
            new_error = ErrorRecord(
                contributor_login=contributor_login,
                error_type="revert",
                related_pr_number=pr_number,
                related_commit_sha=commit_sha,
                revert_count=1,
                bugfix_linked_count=0,
                ci_failure_count=0,
                nightly_failure_count=0,
                weight_score=5.0,
                description=description,
            )
            self.db.add(new_error)
            logger.info(
                "Recorded revert error: login=%s pr=%s sha=%s",
                contributor_login,
                pr_number,
                commit_sha[:8],
            )

    # ── 私有方法：Aggregate Metrics ──────────────────────────────

    async def _upsert_metrics(
        self,
        contributor_aggregates: dict[str, dict[str, Any]],
        since: datetime,
        result: SyncResult,
    ) -> None:
        now = datetime.now(UTC)
        period_start = since.replace(hour=0, minute=0, second=0, microsecond=0).date()
        period_end = now.replace(hour=0, minute=0, second=0, microsecond=0).date()

        for login, agg in contributor_aggregates.items():
            score = self._calculate_contribution_score(agg)
            quadrant = self._calculate_quadrant(score, agg["error_score"])

            stmt = select(ContributorMetrics).where(
                and_(
                    ContributorMetrics.contributor_login == login,
                    ContributorMetrics.period_start == period_start,
                    ContributorMetrics.period_end == period_end,
                )
            )
            db_result = await self.db.execute(stmt)
            existing = db_result.scalar_one_or_none()

            repo_full_name = f"{self.owner}/{self.repo}"
            if existing:
                if not existing.repo_name:
                    existing.repo_name = repo_full_name
                existing.merged_pr_count += agg["merged_pr_count"]
                existing.pending_pr_count += agg["pending_pr_count"]
                existing.code_additions += agg["code_additions"]
                existing.code_deletions += agg["code_deletions"]
                existing.review_count += agg["review_count"]
                existing.approval_count += agg["approval_count"]
                existing.merge_count += agg["merge_count"]
                existing.core_module_count += agg["core_module_count"]
                existing.closed_pr_count += agg["closed_pr_count"]
                existing.error_score += agg["error_score"]
                if score > existing.contribution_score:
                    existing.contribution_score = score
                existing.quadrant = quadrant
                existing.updated_at = now
            else:
                new_metrics = ContributorMetrics(
                    contributor_id=agg["contributor_id"],
                    contributor_login=login,
                    repo_name=repo_full_name,
                    period_start=period_start,
                    period_end=period_end,
                    merged_pr_count=agg["merged_pr_count"],
                    pending_pr_count=agg["pending_pr_count"],
                    code_additions=agg["code_additions"],
                    code_deletions=agg["code_deletions"],
                    review_count=agg["review_count"],
                    approval_count=agg["approval_count"],
                    merge_count=agg["merge_count"],
                    core_module_count=agg["core_module_count"],
                    closed_pr_count=agg["closed_pr_count"],
                    contribution_score=score,
                    error_score=agg["error_score"],
                    quadrant=quadrant,
                )
                self.db.add(new_metrics)

        # 自动推断并更新角色类型
        for login, agg in contributor_aggregates.items():
            roles = detect_roles(
                merged_pr_count=agg["merged_pr_count"],
                review_count=agg["review_count"],
                approval_count=agg["approval_count"],
                merge_count=agg["merge_count"],
                code_additions=agg["code_additions"],
            )

            stmt = select(Contributor).where(Contributor.login == login)
            db_result = await self.db.execute(stmt)
            contributor = db_result.scalar_one_or_none()
            if contributor:
                old_roles = contributor.role_types or []
                if old_roles != roles:
                    contributor.role_types = roles
                    logger.debug("Updated roles for %s: %s -> %s", login, old_roles, roles)

        # 最终提交
        try:
            await self.db.commit()
            result.contributors_synced = len(contributor_aggregates)
            logger.info(
                "Upserted metrics for %d contributors (period=%s~%s)",
                len(contributor_aggregates),
                period_start.isoformat(),
                period_end.isoformat(),
            )
        except Exception as e:
            logger.error("Failed to commit contributor metrics: %s", e)
            await self.db.rollback()
            result.errors.append(f"Failed to commit metrics: {e}")

    @staticmethod
    def _calculate_contribution_score(agg: dict[str, Any]) -> float:
        """计算贡献分数"""
        score = 0.0
        score += agg["merged_pr_count"] * 10.0
        score += agg["code_additions"] * 0.5
        score += agg["review_count"] * 3.0
        score += agg["approval_count"] * 5.0
        score += agg["core_module_count"] * 8.0
        return round(score, 2)

    @staticmethod
    def _calculate_quadrant(contribution_score: float, error_score: float) -> str:
        """计算贡献者位于哪个象限"""
        if contribution_score >= 30 and error_score <= 2:
            return "star"
        elif contribution_score >= 15 and error_score <= 2:
            return "active"
        elif contribution_score < 15 and error_score <= 2:
            return "observer"
        else:
            return "at_risk"

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _parse_datetime(dt_string: str | None) -> datetime | None:
        if not dt_string:
            return None
        try:
            dt_string = dt_string.replace("Z", "+00:00")
            return datetime.fromisoformat(dt_string)
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse datetime '%s': %s", dt_string, e)
            return None

    @staticmethod
    def _get_files_from_commit(commit: dict[str, Any]) -> list[str]:
        """从 GitHub commit 对象中提取变更文件路径列表。

        GitHub /pulls/{n}/commits 接口返回的 commit 对象中，
        files 字段通常不存在。这里返回空列表，由分类器降级到 message-based 分类。
        若将来需要文件级分类，可通过 /repos/{o}/{r}/commits/{sha} 单独拉取。
        """
        commit_data = commit.get("commit", {})
        files: list[dict[str, Any]] | None = commit_data.get("files")
        if isinstance(files, list):
            return [f.get("filename", "") for f in files if f.get("filename")]
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
            combined.errors.extend(result.errors)
        except Exception as e:
            logger.error("Sync failed for repo %s: %s", repo_key, e, exc_info=True)
            combined.errors.append(f"Repo {repo_key}: {e}")

    if combined.synced_at is None:
        combined.synced_at = datetime.now(UTC)

    logger.info(
        "All repos sync finished: total_contributors=%d total_commits=%d total_errors=%d",
        combined.contributors_synced,
        combined.commits_classified,
        len(combined.errors),
    )
    return combined
