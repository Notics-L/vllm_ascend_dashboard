"""
贡献者数据采集模块

从 GitHub 拉取 PR/Commit/Review 数据，执行分类分析，并增量写入数据库。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

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
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    total_prs_scanned: int = 0
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

    def __init__(
        self,
        github_client: GitHubClient,
        db_session: AsyncSession,
        progress_callback=None,
    ):
        self.github = github_client
        self.db = db_session
        self.owner = github_client.owner
        self.repo = github_client.repo
        self.progress_callback = progress_callback

    # ── 入口方法 ──────────────────────────────────────────────────

    async def collect(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> SyncResult:
        start_time = datetime.now(UTC)
        result = SyncResult()

        if since is None:
            since = datetime(2024, 1, 1, tzinfo=UTC)

        if until is None:
            until = datetime.now(UTC)

        logger.info(
            "Starting contributor sync: %s/%s since=%s until=%s",
            self.owner,
            self.repo,
            since.isoformat(),
            until.isoformat(),
        )

        try:
            prs = await self._fetch_prs(since, until)
            result.total_prs_scanned = len(prs)
            logger.info("Fetched %d PRs for contributor sync", len(prs))

            if self.progress_callback:
                await self.progress_callback(5, f"Fetched {len(prs)} PRs, processing...")

            # 每个贡献者每个时间周期内的临时指标聚合
            # Key: (period_start_date, login), Value: aggregate dict
            contributor_period_aggregates: dict[tuple[date, str], dict[str, Any]] = {}

            total_prs = len(prs)
            for idx, pr in enumerate(prs, 1):
                pr_number = pr.get("number")
                if pr_number is None:
                    continue

                if idx % 10 == 0 or idx == total_prs:
                    pct = min(95, int(idx / total_prs * 90) + 5)
                    if self.progress_callback:
                        await self.progress_callback(pct, f"Processing PRs: {idx}/{total_prs}")

                try:
                    await self._process_pr(pr, pr_number, result, contributor_period_aggregates)
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
                    # Commit pending data to prevent session from being poisoned
                    try:
                        await self.db.commit()
                    except Exception as commit_err:
                        logger.debug("Commit failed after PR error, rolling back: %s", commit_err)
                        try:
                            await self.db.rollback()
                        except Exception as rb_err:
                            logger.warning("Rollback also failed for PR #%s: %s", pr_number, rb_err)
                    continue

            # 聚合写入 ContributorMetrics
            try:
                await self._upsert_metrics(contributor_period_aggregates, result)
            except Exception as e:
                logger.error("Failed to upsert metrics: %s", e, exc_info=True)
                result.errors.append(f"Metrics upsert failed: {e}")

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

    async def _fetch_prs(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        try:
            return await self.github.get_pull_requests_by_updated_range(
                self.owner,
                self.repo,
                since,
                until,
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
        contributor_period_aggregates: dict[tuple[date, str], dict[str, Any]],
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
        pr_files: list[dict[str, Any]] = []

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

        try:
            pr_files = await self.github.get_pr_files(self.owner, self.repo, pr_number)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("Failed to fetch files for PR #%s: %s", pr_number, e)

        # From files API: extract file paths and code additions/deletions
        pr_file_paths: list[str] = []
        pr_additions: int = 0
        pr_deletions: int = 0
        for f in pr_files:
            fname = f.get("filename", "")
            if fname:
                pr_file_paths.append(fname)
            pr_additions += f.get("additions") or 0
            pr_deletions += f.get("deletions") or 0

        # 更新 Contributor 记录
        db_contributor = await self._upsert_contributor(user_info, commits, login)
        if db_contributor is None:
            return

        # 按 PR 创建时间的月度周期拆分聚合桶
        pr_created_dt = self._parse_datetime(pr.get("created_at"))
        if pr_created_dt is None:
            pr_created_dt = datetime.now(UTC)
        period_key = date(pr_created_dt.year, pr_created_dt.month, 1)

        def _empty_agg():
            return {
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
                "commit_count": 0,
                "error_score": 0.0,
                "files_changed_count": 0,
                "_commit_types": {},  # commit type -> count mapping
            }

        agg_key = (period_key, login)
        agg = contributor_period_aggregates.setdefault(agg_key, _empty_agg())

        # 更新贡献时间窗口 — 使用 PR 创建时间作为真实贡献时间点
        pr_created = self._parse_datetime(pr.get("created_at"))
        pr_updated = self._parse_datetime(pr.get("updated_at"))
        if pr_created:
            await self._update_contribution_dates(db_contributor, pr_created, pr_updated)

        # 判定 PR 状态
        is_merged = bool(pr.get("merged") or pr.get("merged_at"))
        pr_state = pr.get("state", "closed")

        if is_merged:
            agg["merged_pr_count"] += 1
            # 从 PR files API 获取代码增减量
            agg["code_additions"] += pr_additions
            agg["code_deletions"] += pr_deletions
        elif pr_state == "open":
            agg["pending_pr_count"] += 1
        else:
            agg["closed_pr_count"] += 1

        # 聚合 review 指标 — 采集所有 reviewer（不只是 PR 作者自己）
        for review in reviews:
            review_user = (review.get("user") or {}).get("login", "")
            if not review_user:
                continue
            # 为 reviewer 创建/获取聚合桶（按同一周期）
            reviewer_key = (period_key, review_user)
            reviewer_empty = {
                "contributor_id": None,
                "merged_pr_count": 0,
                "pending_pr_count": 0,
                "code_additions": 0,
                "code_deletions": 0,
                "review_count": 0,
                "approval_count": 0,
                "merge_count": 0,
                "core_module_count": 0,
                "closed_pr_count": 0,
                "commit_count": 0,
                "error_score": 0.0,
                "files_changed_count": 0,
                "_commit_types": {},
                "_is_reviewer_only": True,
            }
            reviewer_agg = contributor_period_aggregates.setdefault(reviewer_key, reviewer_empty)
            reviewer_agg["review_count"] += 1
            if review.get("state") == "APPROVED":
                reviewer_agg["approval_count"] += 1

        # 收集 PR 级别的构建计划信息
        pr_commit_shas: list[str] = []
        pr_commit_types: list[str] = []
        pr_all_files: list[str] = []

        # 对 commits 进行分类
        for commit in commits:
            commit_sha = commit.get("sha", "")
            if not commit_sha:
                continue

            commit_author = (commit.get("author") or {}).get("login", "")
            if commit_author:
                author_key = (period_key, commit_author)
                author_empty = {
                    "contributor_id": None,
                    "merged_pr_count": 0,
                    "pending_pr_count": 0,
                    "code_additions": 0,
                    "code_deletions": 0,
                    "review_count": 0,
                    "approval_count": 0,
                    "merge_count": 0,
                    "core_module_count": 0,
                    "closed_pr_count": 0,
                    "commit_count": 0,
                    "error_score": 0.0,
                    "files_changed_count": 0,
                    "_commit_types": {},
                    "_is_commit_author_only": True,
                }
                author_agg = contributor_period_aggregates.setdefault(author_key, author_empty)
                author_agg["commit_count"] += 1

            commit_info = commit.get("commit", {})
            commit_message = commit_info.get("message", "")
            files_changed = self._get_files_from_commit(commit)
            # Fall back to PR-level file list when commit-level is empty
            if not files_changed and pr_file_paths:
                files_changed = pr_file_paths

            classification_result = classify_commit(commit_message, files_changed)
            committed_at = (commit.get("author") or {}).get("date")
            if not committed_at:
                committed_at = (commit_info.get("committed_date") or commit.get("committed_date"))
            committed_dt = self._parse_datetime(committed_at)
            await self._upsert_commit_classification(
                commit_sha,
                commit_message,
                classification_result,
                login,
                pr_number,
                len(files_changed) if files_changed else 0,
                committed_at=committed_dt,
            )
            result.commits_classified += 1

            # 核心模块计数
            if classification_result.is_core_module:
                agg["core_module_count"] += 1

            # 文件变更数累计
            if files_changed:
                agg["files_changed_count"] += len(files_changed)

            # Commit 类型统计
            commit_type_value = classification_result.commit_type.value
            ct_map: dict[str, int] = agg.get("_commit_types", {})
            ct_map[commit_type_value] = ct_map.get(commit_type_value, 0) + 1
            agg["_commit_types"] = ct_map

            # Revert 检测
            if classification_result.commit_type.value == "Revert":
                original_message = _REVERT_MESSAGE_RE.search(commit_message)
                description = original_message.group(1) if original_message else ""
                await self._record_revert_error(login, pr_number, commit_sha, description, result)
                agg["error_score"] += 1.0

            # 收集构建计划数据
            pr_commit_shas.append(commit_sha)
            pr_commit_types.append(classification_result.commit_type.value)
            if files_changed:
                pr_all_files.extend(files_changed)

        # 为 PR 创建构建计划记录
        try:
            await self._create_build_plan_from_pr(
                pr, pr_number, pr_commit_shas, pr_commit_types,
                list(set(pr_all_files)), login,
            )
            result.build_plans_created += 1
        except Exception as e:
            logger.warning("Failed to create build plan for PR #%s: %s", pr_number, e)

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

    @staticmethod
    def _refresh_contributor(existing: Contributor, login: str, avatar_url: str | None, email: str, name: str, employee_id: str | None) -> None:
        """更新现有 Contributor 记录的字段"""
        existing.login = login
        existing.avatar_url = avatar_url or existing.avatar_url
        existing.email = email or existing.email
        existing.name = name or existing.name
        if employee_id:
            existing.employee_id = employee_id
        existing.updated_at = datetime.now(UTC)

    async def _upsert_contributor(
        self,
        user_info: dict[str, Any],
        commits: list[dict[str, Any]],
        login: str,
    ) -> Contributor | None:
        github_id = user_info.get("id")
        if github_id is not None:
            stmt = select(Contributor).where(Contributor.github_id == github_id)
        else:
            stmt = select(Contributor).where(Contributor.login == login)
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
            self._refresh_contributor(existing, login, avatar_url, email, name, employee_id)
            logger.debug("Updated Contributor %s (id=%s)", login, github_id)
            return existing
        else:
            is_community = not bool(employee_id)
            company = user_info.get("company", "") or ""
            try:
                actual_github_id = github_id if github_id and github_id != 0 else hash(login) % (2**32)
                new_contributor = Contributor(
                    github_id=actual_github_id,
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
                await self.db.flush()
                logger.debug("Created Contributor %s (id=%s, emp=%s)", login, github_id, employee_id)
                return new_contributor
            except Exception as e:
                if "Duplicate entry" in str(e) or "1062" in str(e):
                    await self.db.rollback()
                    lookup = select(Contributor).where(Contributor.login == login)
                    result = await self.db.execute(lookup)
                    existing = result.scalar_one_or_none()
                    if existing:
                        self._refresh_contributor(existing, login, avatar_url, email, name, employee_id)
                        logger.debug("Recovered duplicate Contributor %s (id=%s)", login, github_id)
                        return existing
                    raise
                raise

    @staticmethod
    async def _update_contribution_dates(
        contributor: Contributor,
        pr_created_at: datetime,
        pr_updated_at: datetime | None = None,
    ) -> None:
        """使用 PR 创建时间追踪首次贡献，用 PR 更新时间追踪最后贡献。"""
        first = contributor.first_contribution_at
        if first and first.tzinfo is None:
            first = first.replace(tzinfo=UTC)
        contributor.first_contribution_at = first
        last = contributor.last_contribution_at
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        contributor.last_contribution_at = last
        if contributor.first_contribution_at is None or pr_created_at < contributor.first_contribution_at:
            contributor.first_contribution_at = pr_created_at
        ref_date = pr_updated_at if pr_updated_at else pr_created_at
        if contributor.last_contribution_at is None or ref_date > contributor.last_contribution_at:
            contributor.last_contribution_at = ref_date

    # ── 私有方法：Commit Classification ───────────────────────────

    async def _upsert_commit_classification(
        self,
        commit_sha: str,
        commit_message: str,
        classification: ClassificationResult,
        contributor_login: str,
        pr_number: int,
        files_changed_count: int,
        committed_at: datetime | None = None,
    ) -> None:
        try:
            stmt = select(CommitClassification).where(CommitClassification.commit_sha == commit_sha)
            result = await self.db.execute(stmt)
            rows = result.all()
            # Handle duplicate rows: update first, delete extras
            if rows:
                existing = rows[0][0] if rows[0] else rows[0]
                if isinstance(existing, tuple):
                    existing = existing[0]
                existing.commit_message = commit_message
                existing.commit_type = classification.commit_type.value
                existing.confidence = classification.confidence
                existing.modules_touched = classification.modules_touched
                existing.files_changed_count = files_changed_count
                existing.is_chip_specific = classification.is_chip_specific
                existing.chip_target = classification.chip_target
                existing.contributor_login = contributor_login
                existing.pr_number = pr_number
                existing.repo_name = f"{self.owner}/{self.repo}"
                if committed_at:
                    existing.created_at = committed_at
                # Clean up duplicate rows (keep first)
                for dup in rows[1:]:
                    dup_obj = dup[0] if isinstance(dup, tuple) else dup
                    if dup_obj and dup_obj.id != existing.id:
                        try:
                            await self.db.delete(dup_obj)
                        except Exception as del_err:
                            logger.debug("Failed to remove duplicate commit classification for %s: %s", commit_sha, del_err)
            else:
                try:
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
                        repo_name=f"{self.owner}/{self.repo}",
                        created_at=committed_at,
                    )
                    self.db.add(new_entry)
                except Exception as insert_e:
                    # Handle duplicate commit_sha: another PR may have classified the same commit
                    if "Duplicate entry" in str(insert_e):
                        try:
                            self.db.expunge(new_entry)
                        except Exception as exp_err:
                            logger.debug("Failed to expunge duplicate commit classification for %s: %s", commit_sha, exp_err)
                        return  # Already exists, skip
                    raise
        except Exception as e:
            logger.warning("Failed to upsert commit classification for %s: %s", commit_sha, e)

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

    # ── 私有方法：Build Plan ─────────────────────────────────────

    @staticmethod
    def _derive_build_plan(commit_types: list[str], files_changed: list[str], additions: int, deletions: int) -> tuple[str, float, str]:
        """根据 commit 分类和变更信息推导构建计划。

        Returns:
            (build_type, estimated_saving_percent, reasoning)
        """
        has_doc = "Doc" in commit_types
        has_test = "Test" in commit_types
        has_misc = "Misc" in commit_types
        has_ci = "CI" in commit_types
        has_revert = "Revert" in commit_types
        has_perf = "Performance" in commit_types
        has_feature = "Feature" in commit_types

        modules_lower = [f.lower() for f in files_changed]
        touches_chip = any(p in m for p in ("910", "310", "a2", "a3", "a5", "npu", "cann", "ascend") for m in modules_lower)

        if all(t in ("Doc", "Test", "Misc") for t in commit_types):
            return "skip", 100.0, f"Commit 类型为 {', '.join(sorted(set(commit_types)))}，无需构建"
        if has_revert:
            core = any(km in m for km in ("attention", "kernel", "moe", "mrv2", "eplb") for m in modules_lower)
            if core or touches_chip:
                return "full_build", 0.0, "Revert 涉及核心/NPU 模块，需要全量重建"
            else:
                return "conservative", 40.0, "Revert 非核心模块，保守构建"
        if has_ci and not (has_feature or has_perf):
            return "nightly_only", 70.0, "CI 配置变更，仅需夜间构建验证"
        if has_perf or (has_feature and touches_chip):
            return "full_build", 0.0, "性能优化/新特性涉及 NPU 模块，需要全量构建"
        total_lines = additions + deletions
        if has_feature and total_lines > 500:
            return "full_build", 0.0, "新特性变更量大，需要全量构建"
        if len(files_changed) <= 5:
            return "minimal_build", 60.0, f"仅变更 {len(files_changed)} 个文件，可最小化构建"
        return "smart_check", 50.0, f"Commit 类型 {', '.join(sorted(set(commit_types)))}，智能检查构建"

    async def _create_build_plan_from_pr(
        self,
        pr: dict[str, Any],
        pr_number: int,
        commit_shas: list[str],
        commit_types: list[str],
        files_changed: list[str],
        contributor_login: str,
    ) -> None:
        """为 PR 创建 ImageBuildPlan 记录，created_at 取自 PR 创建时间。"""
        pr_created = self._parse_datetime(pr.get("created_at"))
        additions = pr.get("additions") or 0
        deletions = pr.get("deletions") or 0

        build_type, saving_pct, reasoning = self._derive_build_plan(
            commit_types, files_changed, additions, deletions
        )

        unique_commit_type = commit_types[0] if commit_types else "Misc"

        stmt = select(ImageBuildPlan).where(ImageBuildPlan.pr_number == pr_number)
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.commit_sha = commit_shas[0] if commit_shas else ""
            existing.commit_type = unique_commit_type
            existing.build_type = build_type
            existing.estimated_saving_percent = saving_pct
            existing.reasoning = reasoning
            existing.files_changed = files_changed or []
            existing.created_at = pr_created
            existing.actual_execution = False
            existing.repo_name = f"{self.owner}/{self.repo}"
        else:
            self.db.add(ImageBuildPlan(
                pr_number=pr_number,
                commit_sha=commit_shas[0] if commit_shas else "",
                commit_type=unique_commit_type,
                build_type=build_type,
                chips=[],
                os_list=["ubuntu22.04"],
                cache_strategy="smart_cache",
                estimated_saving_percent=saving_pct,
                actual_execution=False,
                reasoning=reasoning,
                files_changed=files_changed or [],
                repo_name=f"{self.owner}/{self.repo}",
                created_at=pr_created,
            ))

    # ── 私有方法：Aggregate Metrics ──────────────────────────────

    async def _upsert_metrics(
        self,
        contributor_period_aggregates: dict[tuple[date, str], dict[str, Any]],
        result: SyncResult,
    ) -> None:
        now = datetime.now(UTC)

        for (period_start, login), agg in contributor_period_aggregates.items():
            # 计算周期结束日期（下个月的第一天减一天）
            if period_start.month == 12:
                period_end = date(period_start.year + 1, 1, 1)
            else:
                period_end = date(period_start.year, period_start.month + 1, 1)

            # 为纯 reviewer / commit author 补齐 contributor_id（通过 GitHub login 查找或创建）
            is_reviewer_only = agg.pop("_is_reviewer_only", False)
            is_commit_only = agg.pop("_is_commit_author_only", False)
            if agg["contributor_id"] is None and (is_reviewer_only or is_commit_only):
                lookup = select(Contributor).where(Contributor.login == login)
                db_result = await self.db.execute(lookup)
                existing_contrib = db_result.scalar_one_or_none()
                if existing_contrib:
                    agg["contributor_id"] = existing_contrib.id
                else:
                    new_c = Contributor(
                        github_id=hash(login) % (2**32),
                        login=login,
                        avatar_url=None,
                        email=None,
                        name=None,
                        is_community=True,
                        role_types=[],
                        company=None,
                        employee_id=None,
                    )
                    self.db.add(new_c)
                    try:
                        await self.db.flush()
                        agg["contributor_id"] = new_c.id
                        logger.debug("Created Contributor for reviewer/author %s", login)
                    except Exception as e:
                        logger.warning("Failed to create contributor %s: %s, re-lookup", login, e)
                        self.db.expunge(new_c)
                        lookup2 = select(Contributor).where(Contributor.login == login)
                        db_result2 = await self.db.execute(lookup2)
                        fallback = db_result2.scalar_one_or_none()
                        if fallback:
                            agg["contributor_id"] = fallback.id
                        else:
                            agg["contributor_id"] = None
            elif agg.get("contributor_id") is not None:
                # 验证 contributor_id 仍然有效（防止之前的 rollback 使其失效）
                lookup = select(Contributor).where(Contributor.id == agg["contributor_id"])
                db_result = await self.db.execute(lookup)
                existing_contrib = db_result.scalar_one_or_none()
                if not existing_contrib:
                    logger.warning("Contributor id=%s missing for %s, re-lookup by login", agg["contributor_id"], login)
                    lookup2 = select(Contributor).where(Contributor.login == login)
                    db_result2 = await self.db.execute(lookup2)
                    fallback = db_result2.scalar_one_or_none()
                    if fallback:
                        agg["contributor_id"] = fallback.id
                    else:
                        agg["contributor_id"] = None

            if agg.get("contributor_id") is None:
                logger.warning("Skipping metrics for %s (period=%s): no contributor_id", login, period_start)
                continue

            score = self._calculate_contribution_score(agg)
            quadrant = self._calculate_quadrant(score, agg["error_score"])

            # 清理内部字段（不存储到 DB）
            agg.pop("_commit_types", None)

            try:
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
                    self._accumulate_metrics(existing, agg, score, quadrant, now, repo_full_name)
                else:
                    try:
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
                            commit_count=agg.get("commit_count", 0),
                            contribution_score=score,
                            error_score=agg["error_score"],
                            quadrant=quadrant,
                        )
                        self.db.add(new_metrics)
                    except Exception as insert_err:
                        if "Duplicate entry" in str(insert_err) or "IntegrityError" in type(insert_err).__name__:
                            self.db.expunge(new_metrics)
                            logger.debug("Race condition on metrics insert, re-selecting for %s period=%s", login, period_start)
                            await self.db.flush()
                            retry = await self.db.execute(stmt)
                            retry_existing = retry.scalar_one_or_none()
                            if retry_existing:
                                self._accumulate_metrics(retry_existing, agg, score, quadrant, now, repo_full_name)
                            else:
                                raise
                        else:
                            raise
            except Exception as e:
                logger.warning("Failed to upsert metrics for %s (period=%s): %s", login, period_start, e)

        # 自动推断并更新角色类型（聚合所有周期的数据）
        login_totals: dict[str, dict[str, int]] = {}
        for (period_start, login), agg in contributor_period_aggregates.items():
            total = login_totals.setdefault(login, {
                "merged_pr_count": 0, "review_count": 0,
                "approval_count": 0, "merge_count": 0, "code_additions": 0,
            })
            total["merged_pr_count"] += agg["merged_pr_count"]
            total["review_count"] += agg["review_count"]
            total["approval_count"] += agg["approval_count"]
            total["merge_count"] += agg["merge_count"]
            total["code_additions"] += agg["code_additions"]

        for login, total in login_totals.items():
            roles = detect_roles(
                merged_pr_count=total["merged_pr_count"],
                review_count=total["review_count"],
                approval_count=total["approval_count"],
                merge_count=total["merge_count"],
                code_additions=total["code_additions"],
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
            unique_logins = {login for (_, login) in contributor_period_aggregates.keys()}
            result.contributors_synced = len(unique_logins)
            unique_periods = {ps for (ps, _) in contributor_period_aggregates.keys()}
            logger.info(
                "Upserted metrics for %d contributors across %d periods",
                len(unique_logins),
                len(unique_periods),
            )
        except Exception as e:
            logger.error("Failed to commit contributor metrics: %s", e)
            await self.db.rollback()
            result.errors.append(f"Failed to commit metrics: {e}")

    @staticmethod
    def _accumulate_metrics(existing: ContributorMetrics, agg: dict[str, Any], score: float, quadrant: str, now: datetime, repo_full_name: str) -> None:
        """累加聚合数据到现有 metrics 记录"""
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
        existing.commit_count += agg.get("commit_count", 0)
        existing.error_score += agg["error_score"]
        if score > existing.contribution_score:
            existing.contribution_score = score
        existing.quadrant = quadrant
        existing.updated_at = now

    @staticmethod
    def _calculate_contribution_score(agg: dict[str, Any]) -> float:
        """计算贡献分数 — 使用统一评分引擎，支持动态权重和新增维度"""
        from contributor_board.scoring_engine import calculate_contribution_score

        metrics = {
            "merged_pr_count": agg["merged_pr_count"],
            "code_additions": agg["code_additions"],
            "code_deletions": agg["code_deletions"],
            "review_count": agg["review_count"],
            "approval_count": agg["approval_count"],
            "core_module_count": agg["core_module_count"],
            "commit_count": agg.get("commit_count", 0),
            "files_changed_count": agg.get("files_changed_count", 0),
        }
        commit_type_counts = agg.get("_commit_types", {})

        return calculate_contribution_score(metrics, commit_type_counts=commit_type_counts)

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

async def sync_contributors(
    since_date: datetime | None = None,
    until_date: datetime | None = None,
    repo_name: str | None = None,
    progress_callback=None,
) -> SyncResult:
    """执行贡献者数据同步的主入口函数（支持多仓库）

    Args:
        since_date: 增量同步的起始时间，默认为 2024-01-01
        until_date: 增量同步的截止时间，默认为当前时间
        repo_name: 指定仓库名（owner/repo），不传则同步所有仓库
        progress_callback: 异步回调 (percent: int, message: str) -> None

    Returns:
        SyncResult: 多仓库合并后的同步结果统计
    """
    token = settings.GITHUB_TOKEN
    if not token:
        logger.warning("GITHUB_TOKEN is not configured, skipping contributor sync")
        return SyncResult(errors=["GITHUB_TOKEN not configured"])

    all_repos = _resolve_repos()

    if repo_name:
        if "/" in repo_name:
            target_repos = [(repo_name.split("/", 1)[0], repo_name.split("/", 1)[1])]
        else:
            # Allow repo_name without owner prefix — match against configured repos
            matched = [(o, r) for o, r in all_repos if r == repo_name]
            if not matched:
                return SyncResult(errors=[f"Repo not found in configured repos: {repo_name}"])
            target_repos = matched
    else:
        target_repos = all_repos

    logger.info("Contributor sync target repos: %s", target_repos)

    start_time = datetime.now(UTC)
    combined = SyncResult()
    total_repos = len(target_repos)

    async def _sync_single_repo(
        owner: str, repo: str, repo_idx: int, total: int
    ) -> SyncResult:
        """同步单个仓库（用于并行执行）"""
        repo_key = f"{owner}/{repo}"
        repo_base_pct = (repo_idx - 1) / total * 100 if total > 1 else 0
        repo_pct_range = 100 / total if total > 1 else 100

        logger.info("=== Starting sync for repo: %s ===", repo_key)
        result = SyncResult(errors=[f"Repo {repo_key}: repo skipped"])
        try:
            async with GitHubClient(token=token, owner=owner, repo=repo) as github_client:
                async with AsyncSessionLocal() as db:
                    async def repo_progress(pct: int, msg: str):
                        if progress_callback:
                            global_pct = min(95, int(repo_base_pct + (pct / 100) * repo_pct_range))
                            await progress_callback(global_pct, f"[{repo_key}] {msg}")

                    collector = ContributorSyncCollector(github_client, db, progress_callback=repo_progress)
                    result = await collector.collect(since=since_date, until=until_date)
        except Exception as e:
            logger.error("Sync failed for repo %s: %s", repo_key, e, exc_info=True)
            result = SyncResult(errors=[f"Repo {repo_key}: {e}"])
        return result

    # 并行执行所有仓库同步（限制最大并发数为 5）
    import asyncio
    semaphore = asyncio.Semaphore(min(5, total_repos))

    async def _sync_with_semaphore(owner: str, repo: str, idx: int) -> SyncResult:
        async with semaphore:
            return await _sync_single_repo(owner, repo, idx, total_repos)

    tasks = [_sync_with_semaphore(o, r, i) for i, (o, r) in enumerate(target_repos, 1)]
    repo_results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in repo_results:
        if isinstance(result, Exception):
            combined.errors.append(f"Unexpected error: {result}")
        else:
            combined.contributors_synced += result.contributors_synced
            combined.commits_classified += result.commits_classified
            combined.build_plans_created += result.build_plans_created
            combined.total_prs_scanned += result.total_prs_scanned
            combined.errors.extend(result.errors)
            if result.synced_at and (combined.synced_at is None or result.synced_at > combined.synced_at):
                combined.synced_at = result.synced_at

    if progress_callback:
        await progress_callback(98, "Finalizing...")

    if combined.synced_at is None:
        combined.synced_at = datetime.now(UTC)

    end_time = datetime.now(UTC)
    combined.duration_seconds = (end_time - start_time).total_seconds()

    logger.info(
        "All repos sync finished: total_contributors=%d total_commits=%d total_build_plans=%d total_errors=%d",
        combined.contributors_synced,
        combined.commits_classified,
        combined.build_plans_created,
        len(combined.errors),
    )
    return combined
