"""
贡献者数据采集模块（重构版）。

从 GitHub 拉取 PR / commits / reviews / files，按 7 维聚合后写入
contributor_profiles 宽表（每贡献者一行），并保留 ImageBuildPlan 构建计划。

7 维原始指标：
    commits      PR 提交数
    code_files   代码文件变更数（merged PR）
    doc_files    文档文件变更数
    prs          PR 明细（merged / open / closed）
    active_days  近 90 天活跃天数
    review       检视次数 / 批准次数（Reviewer 角色）
    span         首次/最后贡献时间
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from contributor_board.classifier import classify_commit
from contributor_board.role_analytics import detect_roles
from contributor_board.scoring_engine import (
    assign_tier,
    calculate_score,
    infer_role,
)
from infrastructure.clients.github_client import (
    GitHubAPIError,
    GitHubClient,
    GitHubRateLimitError,
)
from infrastructure.core.config import settings
from infrastructure.db.base import SessionLocal as AsyncSessionLocal
from infrastructure.persistence.models import (
    Contributor,
    ContributorProfile,
    ImageBuildPlan,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# "Revert "原 commit message""
_REVERT_MESSAGE_RE = re.compile(r'^Revert\s+"(.+)"', re.DOTALL)
# 从 email / bio 中提取内部工号 (如 Z0012345 / 30012345)
_EMPLOYEE_ID_RE = re.compile(r"\bZ?\d{7,8}\b")

# 文档/方案文件扩展名
_DOC_EXT = {".md", ".rst", ".txt"}
# 文档/方案目录前缀
_DOC_DIR_PREFIXES = ("docs/", "releases/", "release_notes/")


def _is_doc_file(filename: str) -> bool:
    """判断是否为文档/方案文件。"""
    low = filename.lower()
    if any(low.startswith(p) for p in _DOC_DIR_PREFIXES):
        return True
    ext = low[low.rfind("."):]
    return ext in _DOC_EXT


def _empty_profile() -> dict[str, Any]:
    """新建一个空的贡献者画像聚合桶。"""
    return {
        "contributor_id": None,
        "is_reviewer_only": False,
        "commits": 0,
        "code_files": 0,
        "doc_files": 0,
        "pr_merged": 0,
        "pr_open": 0,
        "pr_closed": 0,
        "active_days": set(),
        "first_seen": None,
        "last_seen": None,
        "review_count": 0,
        "approval_count": 0,
        "first_review_hours": None,
        "merge_hours": None,
    }


@dataclass
class SyncResult:
    """同步结果数据类（保持字段名兼容，便于 service/scheduler 读取）。"""

    contributors_synced: int = 0
    commits_classified: int = 0
    build_plans_created: int = 0
    total_prs_scanned: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0
    synced_at: datetime | None = None


class ContributorSyncCollector:
    """贡献者同步采集器（重构版）。

    流程：
    1. 拉取时间范围内的全部 PR
    2. 对每个 PR 拉取 reviews / commits / files
    3. 按 PR 作者和 reviewer 聚合 7 维指标
    4. 写入 ContributorProfile 宽表（每贡献者一行）
    5. 更新 Contributor 主表（身份/角色）
    6. 为 PR 创建 ImageBuildPlan 构建计划（保留）
    """

    def __init__(
        self,
        github_client: GitHubClient,
        db_session: AsyncSession,
        progress_callback=None,
    ) -> None:
        self.github = github_client
        self.db = db_session
        self.owner = github_client.owner
        self.repo = github_client.repo
        self.repo_name = f"{self.owner}/{self.repo}"
        self.progress_callback = progress_callback

    # ── 入口方法 ──────────────────────────────────────────────────

    async def collect(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        commit_profiles: bool = True,
    ) -> SyncResult:
        start_time = datetime.now(UTC)
        result = SyncResult()

        if since is None:
            since = datetime(2025, 1, 1, tzinfo=UTC)
        if until is None:
            until = datetime.now(UTC)

        logger.info(
            "Starting contributor sync: %s since=%s until=%s",
            self.repo_name,
            since.isoformat(),
            until.isoformat(),
        )

        # 每贡献者一个聚合桶（全量重建）
        profiles: dict[str, dict[str, Any]] = {}
        pr_numbers_seen: set[int] = set()

        try:
            prs = await self._fetch_prs(since, until)
            result.total_prs_scanned = len(prs)
            logger.info("Fetched %d PRs for contributor sync", len(prs))
            if self.progress_callback:
                await self.progress_callback(5, f"Fetched {len(prs)} PRs, processing...")

            total = len(prs)
            interrupted = False
            for idx, pr in enumerate(prs, 1):
                pr_number = pr.get("number")
                if pr_number is None:
                    continue
                if idx % 5 == 0 or idx == total:
                    pct = min(95, int(idx / max(1, total) * 90) + 5)
                    if self.progress_callback:
                        await self.progress_callback(pct, f"Processing PRs: {idx}/{total}")
                try:
                    await self._process_pr(pr, pr_number, profiles, pr_numbers_seen, result)
                except GitHubRateLimitError as e:
                    # 限流（客户端层已尝试等待复位，仍失败）：标记为中断，保留旧数据
                    logger.error("Rate limited at PR #%s, aborting rebuild to preserve data: %s", pr_number, e)
                    result.errors.append(f"Rate limit at PR #{pr_number}: {e}")
                    interrupted = True
                    break
                except GitHubAPIError as e:
                    logger.error("API error PR #%s: %s", pr_number, e)
                    result.errors.append(f"API error PR #{pr_number}: {e}")
                except Exception as e:
                    logger.error("Unexpected error PR #%s: %s", pr_number, e, exc_info=True)
                    result.errors.append(f"PR #{pr_number}: {e}")
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass
                    continue

            if interrupted:
                # 关键修复：中途被限流打断 → 回滚事务，绝不清空/只写一半
                # 保留上一份完整画像，反复同步时表现为幂等更新而非数据丢失
                logger.warning("Sync interrupted, rolling back to preserve existing contributor data")
                try:
                    await self.db.rollback()
                except Exception:
                    pass
                if self.progress_callback:
                    await self.progress_callback(99, "Rate limited - aborted to preserve existing data, please re-trigger")
                result.contributors_synced = 0
                result.errors.append("ABORTED: aborted before writing any data to preserve existing state")
                # 跳到统一的收尾逻辑（计算 duration + synced_at）
                pass
            else:
                # 全量重建：在同一事务内清空并写入，commit 才一次性生效（原子化）
                # commit_profiles=False 时，由调用方统一 commit（便于外层原子控制）
                await self._rebuild_profiles(profiles, result, commit_on_success=commit_profiles)

        except Exception as e:
            logger.error("Contributor sync failed: %s", e, exc_info=True)
            result.errors.append(f"Sync failed: {e}")
            try:
                await self.db.rollback()
            except Exception:
                pass

        end_time = datetime.now(UTC)
        result.duration_seconds = (end_time - start_time).total_seconds()
        result.synced_at = end_time
        logger.info(
            "Contributor sync finished: contributors=%d prs=%d errors=%d duration=%.1fs",
            result.contributors_synced, result.total_prs_scanned,
            len(result.errors), result.duration_seconds,
        )
        return result

    # ── 私有方法：PR 拉取 ─────────────────────────────────────────

    async def _fetch_prs(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        try:
            return await self.github.get_pull_requests_by_updated_range(
                self.owner, self.repo, since, until, max_items=None
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
        profiles: dict[str, dict[str, Any]],
        pr_numbers_seen: set[int],
        result: SyncResult,
    ) -> None:
        pr_numbers_seen.add(pr_number)
        user_info = pr.get("user") or {}
        author = user_info.get("login") or ""
        if not author:
            return

        pr_created = self._parse_datetime(pr.get("created_at"))
        pr_merged_at = self._parse_datetime(pr.get("merged_at"))
        pr_state = pr.get("state", "closed")
        is_merged = bool(pr.get("merged_at")) or (pr_state == "closed" and bool(pr.get("merged")))

        # 拉取 reviews / commits / files
        reviews: list[dict[str, Any]] = []
        commits: list[dict[str, Any]] = []
        pr_files: list[dict[str, Any]] = []
        try:
            reviews = await self.github.get_pr_reviews(self.owner, self.repo, pr_number)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("reviews PR #%s failed: %s", pr_number, e)
        try:
            commits = await self.github.get_pr_commits(self.owner, self.repo, pr_number)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("commits PR #%s failed: %s", pr_number, e)
        try:
            pr_files = await self.github.get_pr_files(self.owner, self.repo, pr_number)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.warning("files PR #%s failed: %s", pr_number, e)

        # 文件统计（用去重后的路径集，避免多 commit cherry-pick 重复计数）
        code_paths: set[str] = set()
        doc_paths: set[str] = set()
        for f in pr_files:
            fname = f.get("filename", "")
            if not fname:
                continue
            (doc_paths if _is_doc_file(fname) else code_paths).add(fname)

        # 活跃天数：PR 创建日 + 每个提交日 + 检视日
        active_days: set[date] = set()
        if pr_created:
            active_days.add(pr_created.date())

        # ── PR 作者 ──
        author_email = ""
        commit_shas: list[str] = []
        commit_types: list[str] = []
        for commit in commits:
            sha = commit.get("sha", "")
            if not sha:
                continue
            commit_shas.append(sha)
            cmsg = (commit.get("commit") or {}).get("message", "")
            ccls = classify_commit(cmsg, [])
            commit_types.append(ccls.commit_type.value)
            commit_author = (commit.get("author") or {}).get("login", "")
            if not commit_author:
                commit_author = author
            cdt = self._parse_datetime((commit.get("author") or {}).get("date"))
            if cdt:
                active_days.add(cdt.date())
            email = (commit.get("commit", {}).get("author") or {}).get("email", "")
            if email and commit_author == author:
                author_email = email

        author_profile = self._ensure_profile(profiles, author, user_info)
        author_profile["active_days"] |= active_days  # type: ignore[operator]
        if pr_created:
            self._touch_profile(author_profile, pr_created)

        # 文件/提交计数（仅 merged PR 计入 code/doc files，避免未合入改动污染质量维）
        if is_merged:
            author_profile["code_files"] += len(code_paths)
            author_profile["doc_files"] += len(doc_paths)
            author_profile["pr_merged"] += 1
            if pr_merged_at:
                created = pr_created or pr_merged_at
                if created and pr_merged_at > created:
                    hours = (pr_merged_at - created).total_seconds() / 3600
                    if author_profile["merge_hours"] is None:
                        author_profile["merge_hours"] = hours
                    else:
                        author_profile["merge_hours"] += hours
        elif pr_state == "open":
            author_profile["pr_open"] += 1
        else:
            author_profile["pr_closed"] += 1

        author_profile["commits"] += len(commits)
        result.commits_classified += len(commits)
        author_email_set = author_email
        if author_email_set:
            self._set_email(author_profile, author_email_set)

        # ── Reviewers ──
        for review in reviews:
            rv_login = (review.get("user") or {}).get("login", "")
            if not rv_login or rv_login == "app":
                continue
            rv_profile = self._ensure_profile(profiles, rv_login, None, reviewer_only=True)
            rv_profile["review_count"] += 1
            if review.get("state") == "APPROVED":
                rv_profile["approval_count"] += 1
            rdt = self._parse_datetime(review.get("submitted_at"))
            if rdt:
                rv_profile["active_days"].add(rdt.date())
                if author_profile.get("_first_review_anchor") is None:
                    author_profile["_first_review_anchor"] = rdt
                # 首次检视响应时长
                if pr_created and rdt > pr_created:
                    hours = (rdt - pr_created).total_seconds() / 3600
                    if author_profile["first_review_hours"] is None:
                        author_profile["first_review_hours"] = hours
                    else:
                        author_profile["first_review_hours"] = min(author_profile["first_review_hours"], hours)

        # ── 构建计划（保留）──
        # Build Plan 失败不应影响画像聚合：若让它冒泡到 collect 的 except 分支，
        # 会触发共享 session 的 db.rollback()，连带撤销未提交的 DELETE/画像，
        # 导致后续 INSERT 撞上残留行报 1062。这里就地捕获并记录。
        try:
            await self._create_build_plan_from_pr(
                pr, pr_number, commit_shas, commit_types, sorted(code_paths | doc_paths), result
            )
        except Exception as e:
            logger.warning("build plan PR #%s skipped: %s", pr_number, e)
            result.errors.append(f"build plan PR #{pr_number}: {e}")

    # ── 私有方法：画像聚合 ─────────────────────────────────────────

    def _ensure_profile(
        self,
        profiles: dict[str, dict[str, Any]],
        login: str,
        user_info: dict[str, Any] | None = None,
        reviewer_only: bool = False,
    ) -> dict[str, Any]:
        if login not in profiles:
            p = _empty_profile()
            if reviewer_only:
                p["is_reviewer_only"] = True
            if user_info:
                p["_github_id"] = user_info.get("id")
                p["_name"] = user_info.get("name")
                p["_avatar_url"] = user_info.get("avatar_url")
            profiles[login] = p
        return profiles[login]

    def _set_email(self, profile: dict[str, Any], email: str) -> None:
        profile["_email"] = email  # type: ignore[assignment]

    @staticmethod
    def _touch_profile(profile: dict[str, Any], dt: datetime | None) -> None:
        if profile is None or dt is None:
            return
        d = dt.date()
        profile["active_days"].add(d)  # type: ignore[union-attr]
        first = profile.get("first_seen")
        last = profile.get("last_seen")
        if first is None or d < first:
            profile["first_seen"] = d
        if last is None or d > last:
            profile["last_seen"] = d

    # ── 私有方法：Contributor 主表 ─────────────────────────────────

    @staticmethod
    def _extract_employee_id(user_info: dict[str, Any], email: str) -> str | None:
        for text in (
            email, user_info.get("email", ""), user_info.get("bio", ""),
            user_info.get("company", ""), user_info.get("name", ""),
            user_info.get("login", ""),
        ):
            if text:
                matches = _EMPLOYEE_ID_RE.findall(text)
                if matches:
                    return matches[0]
        return None

    async def _upsert_contributor(
        self,
        login: str,
        user_info: dict[str, Any] | None,
        email: str,
    ) -> Contributor | None:
        """幂等 upsert：在独立事务中查询/创建 Contributor，避免污染主 session。

        使用独立 AsyncSession 做 contributor 的 SELECT+INSERT+COMMIT，
        与主 session（负责 build plan + profile 写入）完全隔离。
        即使 contributor 表出现 1062，也只影响独立 session，
        绝不触发主 session 的 rollback。
        """
        user_info = user_info or {}
        github_id = user_info.get("id")
        name = user_info.get("name", "")
        avatar_url = user_info.get("avatar_url", "")
        employee_id = self._extract_employee_id(user_info, email)
        actual_github_id = github_id if github_id and github_id != 0 else hash(login) % (2**32)

        async with AsyncSessionLocal() as cdb:
            try:
                if github_id is not None:
                    stmt = select(Contributor).where(Contributor.github_id == github_id)
                else:
                    stmt = select(Contributor).where(Contributor.login == login)
                result = await cdb.execute(stmt)
                existing = result.scalars().first()

                if existing:
                    if name:
                        existing.name = name
                    if avatar_url:
                        existing.avatar_url = avatar_url or existing.avatar_url
                    if email:
                        existing.email = email
                    if employee_id:
                        existing.employee_id = employee_id
                    existing.updated_at = datetime.now(UTC)
                    await cdb.commit()
                    return existing

                is_community = not bool(employee_id)
                new_c = Contributor(
                    github_id=actual_github_id,
                    login=login,
                    avatar_url=avatar_url or None,
                    email=email or None,
                    name=name or None,
                    is_community=is_community,
                    role_types=[],
                    company=(user_info.get("company") or None),
                    employee_id=employee_id,
                )
                cdb.add(new_c)
                await cdb.commit()
                await cdb.refresh(new_c)
                return new_c
            except Exception:
                await cdb.rollback()
                # 1062 兜底：按 login 重新查询（不触发主 session rollback）
                lookup = select(Contributor).where(Contributor.login == login)
                r = await cdb.execute(lookup)
                existing = r.scalars().first()
                if existing:
                    if name:
                        existing.name = name
                    if avatar_url:
                        existing.avatar_url = avatar_url or existing.avatar_url
                    if email:
                        existing.email = email
                    await cdb.commit()
                    return existing
                raise

    # ── 私有方法：全量重建画像表 ───────────────────────────────────

    async def _rebuild_profiles(
        self,
        profiles: dict[str, dict[str, Any]],
        result: SyncResult,
        *,
        commit_on_success: bool = True,
    ) -> None:
        now = datetime.now(UTC)
        active_cutoff = (now - timedelta(days=90)).date()

        # 1. 清空本仓库旧画像
        await self.db.execute(
            delete(ContributorProfile).where(ContributorProfile.repo_name == self.repo_name)
        )
        logger.info("Cleared %s contributor_profiles rows for rebuild", self.repo_name)

        contributor_cache: dict[str, Contributor | None] = {}
        created = 0
        for login, prof in profiles.items():
            email = prof.pop("_email", "") or ""
            pr_total = prof["pr_merged"] + prof["pr_open"] + prof["pr_closed"]
            # 近 90 天活跃天数
            active_days_90 = sum(1 for d in prof["active_days"] if d >= active_cutoff)
            # 持续期
            span_days = 0
            if prof["first_seen"] and prof["last_seen"]:
                span_days = (prof["last_seen"] - prof["first_seen"]).days

            # Reviewer / 检视指标
            review_count = prof["review_count"]
            approval_count = prof["approval_count"]
            review_ratio = round(review_count / max(1, review_count + pr_total), 4)

            # Contributor 主表（身份 + 角色）
            if login not in contributor_cache:
                contributor_cache[login] = await self._upsert_contributor(
                    login,
                    {"id": prof.get("_github_id"), "name": prof.get("_name"),
                     "avatar_url": prof.get("_avatar_url")},
                    email,
                )
            contributor = contributor_cache[login]
            contributor_id = contributor.id if contributor else None

            # 角色推断
            roles = detect_roles(
                merged_pr_count=prof["pr_merged"],
                review_count=review_count,
                approval_count=approval_count,
                merge_count=prof["pr_merged"],
                code_additions=prof["code_files"],
            )
            if contributor and contributor.role_types != roles:
                contributor.role_types = roles

            # 7 维评分
            metrics = {
                "commit_count": prof["commits"],
                "code_files": prof["code_files"],
                "doc_files": prof["doc_files"],
                "pr_merged": prof["pr_merged"],
                "pr_open": prof["pr_open"],
                "pr_closed": prof["pr_closed"],
                "active_days_90": active_days_90,
                "span_days": span_days,
                "review_count": review_count,
                "approval_count": approval_count,
            }
            score = calculate_score(metrics)
            tier = assign_tier(score, active_days_90)
            role = infer_role(metrics)

            profile_row = ContributorProfile(
                repo_name=self.repo_name,
                login=login,
                contributor_id=contributor_id,
                commit_count=prof["commits"],
                code_files=prof["code_files"],
                doc_files=prof["doc_files"],
                pr_merged=prof["pr_merged"],
                pr_open=prof["pr_open"],
                pr_closed=prof["pr_closed"],
                active_days_90=active_days_90,
                span_days=span_days,
                review_count=review_count,
                approval_count=approval_count,
                review_ratio=review_ratio,
                total_score=score,
                tier=tier,
                role=role,
                first_seen=prof["first_seen"],
                last_seen=prof["last_seen"],
            )
            self.db.add(profile_row)
            created += 1

        if commit_on_success:
            await self.db.commit()
        result.contributors_synced = created
        logger.info(
            "Rebuilt contributor_profiles: %d rows for %s (commit=%s)",
            created, self.repo_name, commit_on_success,
        )

    # ── 私有方法：Build Plan（保留）────────────────────────────────

    @staticmethod
    def _derive_build_plan(
        commit_types: list[str], files_changed: list[str], additions: int, deletions: int
    ) -> tuple[str, float, str]:
        has_doc = "Doc" in commit_types
        has_test = "Test" in commit_types
        has_misc = "Misc" in commit_types
        has_ci = "CI" in commit_types
        has_revert = "Revert" in commit_types
        has_perf = "Performance" in commit_types
        has_feature = "Feature" in commit_types

        modules_lower = [f.lower() for f in files_changed]
        touches_chip = any(
            p in m for p in ("910", "310", "a2", "a3", "a5", "npu", "cann", "ascend") for m in modules_lower
        )
        if all(t in ("Doc", "Test", "Misc") for t in commit_types):
            return "skip", 100.0, f"Commit 类型为 {', '.join(sorted(set(commit_types)))}，无需构建"
        if has_revert:
            core = any(km in m for km in ("attention", "kernel", "moe", "mrv2", "eplb") for m in modules_lower)
            if core or touches_chip:
                return "full_build", 0.0, "Revert 涉及核心/NPU 模块，需要全量重建"
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
        result: SyncResult,
    ) -> None:
        pr_created = self._parse_datetime(pr.get("created_at"))
        additions = pr.get("additions") or 0
        deletions = pr.get("deletions") or 0

        build_type, saving_pct, reasoning = self._derive_build_plan(
            commit_types, files_changed, additions, deletions
        )
        unique_commit_type = commit_types[0] if commit_types else "Misc"

        # 按 (pr_number, repo_name) 精确匹配；历史数据中同一 pr_number 可能因
        # 多 repo 口径/多次同步堆积多行，故取 id 最大的最新一行作为 upsert 目标，
        # 避免 scalar_one_or_none() 在多行时抛 "Multiple rows were found"。
        stmt = (
            select(ImageBuildPlan)
            .where(ImageBuildPlan.pr_number == pr_number)
            .where(ImageBuildPlan.repo_name == self.repo_name)
            .order_by(ImageBuildPlan.id.desc())
        )
        qr = await self.db.execute(stmt)
        existing = qr.scalars().first()

        if existing:
            existing.commit_sha = commit_shas[0] if commit_shas else ""
            existing.commit_type = unique_commit_type
            existing.build_type = build_type
            existing.estimated_saving_percent = saving_pct
            existing.reasoning = reasoning
            existing.files_changed = files_changed or []
            if pr_created:
                existing.created_at = pr_created
            existing.actual_execution = False
            existing.repo_name = self.repo_name
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
                repo_name=self.repo_name,
                created_at=pr_created,
            ))
            result.build_plans_created += 1

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _parse_datetime(dt_string: str | None) -> datetime | None:
        if not dt_string:
            return None
        try:
            return datetime.fromisoformat(str(dt_string).replace("Z", "+00:00"))
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse datetime '%s': %s", dt_string, e)
            return None


# ── 仓库解析 ─────────────────────────────────────────────────────

def _resolve_repos() -> list[tuple[str, str]]:
    """解析需要采集的仓库列表。"""
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
    """执行贡献者数据同步的主入口（多仓库，并行）。"""
    import asyncio

    token = settings.GITHUB_TOKEN
    if not token:
        logger.warning("GITHUB_TOKEN is not configured, skipping contributor sync")
        return SyncResult(errors=["GITHUB_TOKEN not configured"])

    all_repos = _resolve_repos()
    if repo_name:
        if "/" in repo_name:
            target_repos = [repo_name.split("/", 1)]
        else:
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
    semaphore = asyncio.Semaphore(min(5, total_repos))

    async def _sync_single_repo(owner: str, repo: str, repo_idx: int, total: int) -> SyncResult:
        repo_key = f"{owner}/{repo}"
        repo_base_pct = (repo_idx - 1) / total * 100 if total > 1 else 0
        repo_pct_range = 100 / total if total > 1 else 100
        logger.info("=== Starting sync for repo: %s ===", repo_key)
        result = SyncResult(errors=[f"Repo {repo_key}: skipped"])
        try:
            async with GitHubClient(token=token, owner=owner, repo=repo) as github_client:
                async with AsyncSessionLocal() as db:
                    async def repo_progress(pct: int, msg: str):
                        if progress_callback:
                            global_pct = min(95, int(repo_base_pct + (pct / 100) * repo_pct_range))
                            await progress_callback(global_pct, f"[{repo_key}] {msg}")
                    collector = ContributorSyncCollector(github_client, db, progress_callback=repo_progress)
                    # collect 不再自己提交，由外层统一 commit，实现 Build Plan + 画像原子化
                    result = await collector.collect(since=since_date, until=until_date, commit_profiles=False)
                    if result.errors and not result.contributors_synced:
                        # 被中断/完全失败：直接回滚，保留上一份数据
                        logger.warning("Sync aborted for %s (no profiles synced), rolling back repo data", f"{owner}/{repo}")
                        try:
                            await db.rollback()
                        except Exception:
                            pass
                    else:
                        await db.commit()
        except Exception as e:
            logger.error("Sync failed for repo %s: %s", repo_key, e, exc_info=True)
            result = SyncResult(errors=[f"Repo {repo_key}: {e}"])
        return result

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
    combined.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()

    logger.info(
        "All repos sync finished: contributors=%d commits=%d build_plans=%d errors=%d",
        combined.contributors_synced, combined.commits_classified,
        combined.build_plans_created, len(combined.errors),
    )
    return combined
