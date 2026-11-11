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

import asyncio
import json
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
    ContributorSyncCheckpoint,
    DailyCommit,
    DailyPR,
    ImageBuildPlan,
)
from infrastructure.tasks.background_task import BackgroundTaskTracker
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# ── 日度明细补录常量 ─────────────────────────────────────────────
DAILY_FLUSH_EVERY = 50   # 每处理 N 个 PR 提交一次 DB（避免 session 长期持有大量未提交对象）
DAILY_CANCEL_CHECK_EVERY = 20  # 每 N 个 PR 检查一次取消状态
DAILY_CHECKPOINT_EVERY = 100  # 每处理 N 个 PR 落一次断点（供 cancel/限流后恢复）

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
        cancel_check_func=None,
    ) -> None:
        self.github = github_client
        self.db = db_session
        self.owner = github_client.owner
        self.repo = github_client.repo
        self.repo_name = f"{self.owner}/{self.repo}"
        self.progress_callback = progress_callback
        self.cancel_check_func = cancel_check_func
        # 日度明细追踪（本次同步扫到的 PR + 每个 PR 的 commit sha），
        # 用于收尾前清理本仓库 stale 日度数据，保证窗口一致、无重复
        self._daily_pr_numbers: set[int] = set()
        self._daily_commit_shas: set[str] = set()

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

        # ── 尝试恢复断点 ──
        resumed = False
        last_processed_pr = 0
        cp = await self._load_checkpoint(since, until)
        if cp:
            profiles, pr_numbers_seen, last_processed_pr = cp
            resumed = True
            logger.info(
                "Resuming from checkpoint: %d PRs done, %d contributors aggregated",
                len(pr_numbers_seen), len(profiles),
            )
            # 断点恢复后内存里的 daily 追踪集合是空的，从 DB 恢复
            await self._finalize_daily_from_db()
        else:
            profiles: dict[str, dict[str, Any]] = {}
            pr_numbers_seen: set[int] = set()

        interrupted = False
        cancelled = False
        skipped = 0

        try:
            prs = await self._fetch_prs(since, until)
            result.total_prs_scanned = len(prs)
            if not prs and not resumed:
                logger.warning(
                    "Fetched 0 PRs for %s (since=%s until=%s) — likely API error, "
                    "invalid token, or genuinely empty range",
                    self.repo_name, since.isoformat(), until.isoformat(),
                )
            else:
                logger.info(
                    "Fetched %d PRs for contributor sync (%s)",
                    len(prs),
                    "resuming from checkpoint" if resumed else "full scan",
                )
            if self.progress_callback:
                await self.progress_callback(5, f"Fetched {len(prs)} PRs, processing...")

            total = len(prs)
            for idx, pr in enumerate(prs, 1):
                pr_number = pr.get("number")
                if pr_number is None:
                    continue

                # 定期检查取消状态（20 个 PR 一次，避免高频 DB 查询）
                if self.cancel_check_func and idx % DAILY_CANCEL_CHECK_EVERY == 0:
                    try:
                        if await self.cancel_check_func():
                            logger.info("Contributor sync cancelled at PR #%s", pr_number)
                            cancelled = True
                            try:
                                await self.db.rollback()
                            except Exception:
                                pass
                            if self.progress_callback:
                                await self.progress_callback(
                                    99, f"Cancelled at PR #{pr_number} ({idx}/{total})"
                                )
                            if not interrupted:
                                result.errors.append(
                                    f"CANCELLED at PR #{pr_number} ({idx}/{total} done)"
                                )
                            break
                    except Exception:
                        pass

                # 断点续跑：跳过已完成的 PR
                if resumed and pr_number in pr_numbers_seen:
                    skipped += 1
                    continue

                if idx % 5 == 0 or idx == total:
                    pct = min(95, int(idx / max(1, total) * 90) + 5)
                    if self.progress_callback:
                        await self.progress_callback(pct, f"Processing PRs: {idx}/{total}")
                try:
                    await self._process_pr(pr, pr_number, profiles, pr_numbers_seen, result)
                    last_processed_pr = pr_number
                except GitHubRateLimitError as e:
                    # 限流（客户端层已等待 RESET 窗口仍失败）：
                    # 保存断点 + 回滚未提交的 DB 变更，重新触发时从断点续跑
                    logger.error(
                        "Rate limited at PR #%s, saving checkpoint and aborting: %s",
                        pr_number, e,
                    )
                    result.errors.append(f"Rate limit at PR #{pr_number}: {e}")
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass
                    try:
                        await self._save_checkpoint(
                            profiles, pr_numbers_seen,
                            last_processed_pr, total,
                            since, until,
                            len(result.errors),
                            str(e),
                        )
                    except Exception as ckpt_exc:
                        logger.error("Failed to save checkpoint: %s", ckpt_exc)
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
                logger.warning("Sync interrupted by rate limit, checkpoint saved for resume")
                if self.progress_callback:
                    await self.progress_callback(
                        99,
                        f"Rate limited at PR #{last_processed_pr} - checkpoint saved, "
                        f"re-trigger to resume ({skipped}/{total} already done)",
                    )
                result.contributors_synced = 0
                result.errors.append(
                    f"INTERRUPTED: rate limit at PR #{last_processed_pr}, "
                    f"checkpoint saved ({len(pr_numbers_seen)}/{total} PRs done)"
                )
            elif cancelled:
                logger.warning("Sync cancelled by user at PR #%s", last_processed_pr)
                result.contributors_synced = 0
                # 错误已在上面 cancel_check_func 检测时追加
            else:
                if skipped:
                    logger.info("Skipped %d already-processed PRs from checkpoint", skipped)
                # 全量重建：在同一事务内清空并写入，commit 才一次性生效（原子化）
                # commit_profiles=False 时，由调用方统一 commit（便于外层原子控制）
                await self._rebuild_profiles(profiles, result, commit_on_success=commit_profiles)
                # 清理本次同步窗口之外 / 已消失的日度明细，保证 daily 表与本次 PR 集一致
                await self._cleanup_stale_daily()
                # 同步完成：清除断点
                await self._clear_checkpoint()

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
            "Contributor sync finished: contributors=%d prs=%d errors=%d duration=%.1fs%s",
            result.contributors_synced, result.total_prs_scanned,
            len(result.errors), result.duration_seconds,
            " (resumed)" if resumed else "",
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

    async def _fetch_pr_details(
        self, pr_number: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """并发拉取单个 PR 的 reviews / commits / files（3 路并行）。"""
        return await self.github.get_pr_details_concurrent(
            self.owner, self.repo, pr_number
        )

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

        # 并发拉取 reviews / commits / files（优先使用批量预取缓存）
        reviews, commits, pr_files = await self._fetch_pr_details(pr_number)

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

        # ── 日度明细（贡献者详情页趋势/热力图/分布/时间线）──
        # 注意：_write_daily_pr / _write_daily_commits 是 async 方法，必须 await
        # 否则 coroutine 被创建但从不执行，日度数据全部丢失
        try:
            await self._write_daily_pr(pr, pr_number, pr_state, is_merged, pr_created, pr_merged_at, commits, reviews)
        except Exception as e:
            logger.warning("daily PR #%s write skipped: %s", pr_number, e)
            result.errors.append(f"daily PR #{pr_number}: {e}")
        try:
            await self._write_daily_commits(pr, pr_number, author, commits, pr_files, reviews)
        except Exception as e:
            logger.warning("daily commits PR #%s write skipped: %s", pr_number, e)
            result.errors.append(f"daily commits PR #{pr_number}: {e}")

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

    # ── 私有方法：日度明细写入（贡献者详情页图表）────────────────────

    async def _write_daily_pr(
        self,
        pr: dict[str, Any],
        pr_number: int,
        pr_state: str,
        is_merged: bool,
        pr_created: datetime | None,
        pr_merged_at: datetime | None,
        commits: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
    ) -> None:
        """把当前 PR 按 created_at 的 data_date 写入 daily_prs（幂等 upsert）。"""
        if pr_created is None:
            pr_created = pr_merged_at or datetime.now(UTC)
        data_date = pr_created.date()
        project = self.repo_name

        user_info = pr.get("user") or {}
        author = user_info.get("login") or ""
        if not author:
            self._daily_pr_numbers.add(pr_number)
            return

        # 拉取 PR 关联的 commit sha 列表（用于 commits JSON 列 + 后续清理追踪）
        pr_commit_shas: list[str] = []
        for c in commits:
            sha = c.get("sha", "")
            if sha:
                pr_commit_shas.append(sha)
        self._daily_pr_numbers.add(pr_number)
        self._daily_commit_shas.update(pr_commit_shas)

        stmt = (
            select(DailyPR)
            .where(DailyPR.project == project)
            .where(DailyPR.pr_number == pr_number)
            .order_by(DailyPR.id.desc())
        )
        qr = await self.db.execute(stmt)
        row = qr.scalars().first()

        title = pr.get("title") or f"PR #{pr_number}"
        html_url = pr.get("html_url") or f"https://github.com/{project}/pull/{pr_number}"
        labels = [l.get("name") for l in (pr.get("labels") or []) if l.get("name")]
        body = pr.get("body") or ""

        if row is None:
            row = DailyPR(
                project=project,
                pr_number=pr_number,
                title=title,
                state=pr_state,
                author=author,
                created_at=pr_created,
                merged_at=pr_merged_at,
                html_url=html_url,
                labels=labels,
                body=body,
                commits=pr_commit_shas,
                data_date=data_date,
                fetched_at=datetime.now(UTC),
            )
            self.db.add(row)
        else:
            row.title = title
            row.state = pr_state
            row.author = author
            row.created_at = pr_created
            row.merged_at = pr_merged_at
            row.html_url = html_url
            row.labels = labels
            row.body = body
            row.commits = pr_commit_shas
            row.data_date = data_date
            row.fetched_at = datetime.now(UTC)

    async def _write_daily_commits(
        self,
        pr: dict[str, Any],
        pr_number: int,
        author: str,
        commits: list[dict[str, Any]],
        pr_files: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
    ) -> None:
        """把每个 commit 按其 author.date 的 data_date 写入 daily_commits。

        去重：同一个 sha 在同一 PR 里只写一次（GitHub /commits 已去重）。
        跨 PR 的同一 sha（cherry-pick）会保留多行（不同 pr_number），属正常。
        代码行增删：优先取 commit-level patch 行数（如果 API 已带）；
        否则用 PR 级 additions/deletions 按 commit 数均摊（近似，避免空图）。
        """
        project = self.repo_name
        if not commits:
            return

        pr_total_add = pr.get("additions") or 0
        pr_total_del = pr.get("deletions") or 0
        # 均摊到每个 commit（无 patch 级别的精确行数时用）
        per_commit_add = pr_total_add / len(commits)
        per_commit_del = pr_total_del / len(commits)

        pr_title = pr.get("title") or f"PR #{pr_number}"
        pr_desc = pr.get("body") or ""
        html_url_pr = pr.get("html_url") or f"https://github.com/{project}/pull/{pr_number}"

        for commit in commits:
            sha = commit.get("sha", "")
            if not sha:
                continue
            self._daily_commit_shas.add(sha)

            cinfo = commit.get("commit") or {}
            cmsg = cinfo.get("message") or ""
            c_author_github = (commit.get("author") or {}).get("login") or author
            c_author_email = (cinfo.get("author") or {}).get("email") or ""
            c_committed_dt = (cinfo.get("author") or {}).get("date")
            c_committed = self._parse_datetime(c_committed_dt)
            # GitHub /commits 返回的 committed_date 在 commit.committed_date 字段
            c_committed_date = cinfo.get("committed_date")
            c_committed = self._parse_datetime(c_committed_date) or c_committed

            if c_committed is None:
                continue
            data_date = c_committed.date()

            # 增删行数：commit 级 patch 行（如有）否则均摊
            patch_lines = commit.get("stats") or {}
            additions = int(patch_lines.get("additions") or per_commit_add)
            deletions = int(patch_lines.get("deletions") or per_commit_del)

            # 文件变更：PR 级（commit 级通常没有）
            files_list: list[str] = [f.get("filename", "") for f in pr_files if f.get("filename")]

            short_sha = sha[:7]
            commit_html = f"https://github.com/{project}/commit/{sha}"

            stmt = (
                select(DailyCommit)
                .where(DailyCommit.project == project)
                .where(DailyCommit.sha == sha)
                .order_by(DailyCommit.id.desc())
            )
            qr = await self.db.execute(stmt)
            row = qr.scalars().first()

            if row is None:
                row = DailyCommit(
                    project=project,
                    sha=sha,
                    short_sha=short_sha,
                    message=cmsg[:1000],
                    full_message=cmsg,
                    author=c_author_github,
                    author_email=c_author_email or None,
                    committed_at=c_committed,
                    html_url=commit_html,
                    pr_number=pr_number,
                    pr_title=pr_title,
                    pr_description=pr_desc,
                    files_changed=files_list,
                    additions=additions,
                    deletions=deletions,
                    data_date=data_date,
                    fetched_at=datetime.now(UTC),
                )
                self.db.add(row)
            else:
                row.message = cmsg[:1000]
                row.full_message = cmsg
                row.author = c_author_github
                row.author_email = c_author_email or None
                row.committed_at = c_committed
                row.pr_number = pr_number
                row.pr_title = pr_title
                row.pr_description = pr_desc
                row.files_changed = files_list
                row.additions = additions
                row.deletions = deletions
                row.data_date = data_date
                row.fetched_at = datetime.now(UTC)

    async def _cleanup_stale_daily(self) -> None:
        """删除本仓库中：
        1. sha 不在本次 commit 集里的 daily_commits 行（PR 被合入后 commit 集外的残留）
        2. pr_number 不在本次 PR 集里的 daily_prs 行

        这是幂等 upsert + 全量重建语义下对 stale 数据的清理。
        仅删除本仓库（project=self.repo_name）的行，绝不动其它仓库。
        """
        if self._daily_pr_numbers:
            await self.db.execute(
                delete(DailyPR).where(
                    DailyPR.project == self.repo_name,
                    DailyPR.pr_number.not_in(self._daily_pr_numbers),
                )
            )
        if self._daily_commit_shas:
            # 批量 not_in 在 MySQL 有 65535 参数上限，分块处理
            shas = list(self._daily_commit_shas)
            for i in range(0, len(shas), 5000):
                chunk = shas[i:i + 5000]
                await self.db.execute(
                    delete(DailyCommit).where(
                        DailyCommit.project == self.repo_name,
                        DailyCommit.sha.not_in(chunk),
                    )
                )
        logger.info(
            "Cleaned stale daily rows for %s (prs=%d, commits=%d)",
            self.repo_name, len(self._daily_pr_numbers), len(self._daily_commit_shas),
        )

    # ── 私有方法：断点持久化（跨额度窗口续跑）───────────────────────

    @staticmethod
    def _serialize_profiles(profiles: dict[str, dict[str, Any]]) -> str:
        """把 profiles dict 序列化为 JSON（set 转 sorted list，date 转 iso）。"""

        def _conv(obj: Any) -> Any:
            if isinstance(obj, set):
                return sorted(str(x) for x in obj)
            if isinstance(obj, date):
                return obj.isoformat()
            if isinstance(obj, datetime):
                return obj.isoformat()
            return str(obj)

        return json.dumps(profiles, default=_conv, ensure_ascii=False)

    @staticmethod
    def _deserialize_profiles(raw: str) -> dict[str, dict[str, Any]]:
        """反序列化 profiles JSON，还原 set / date 对象。"""
        data: dict[str, dict[str, Any]] = json.loads(raw)
        for prof in data.values():
            ad = prof.get("active_days")
            if isinstance(ad, list):
                prof["active_days"] = {date.fromisoformat(s) for s in ad}
            for key in ("first_seen", "last_seen"):
                val = prof.get(key)
                if isinstance(val, str):
                    prof[key] = date.fromisoformat(val)
        return data

    async def _save_checkpoint(
        self,
        profiles: dict[str, dict[str, Any]],
        pr_numbers_done: set[int],
        last_processed_pr: int,
        total_prs: int,
        since: datetime,
        until: datetime,
        errors_count: int,
        abort_reason: str,
    ) -> None:
        """在独立 session 中保存断点（主 session 可能已 rollback）。

        仅保存 checkpoint_kind='full_sync'，与 daily_backfill 类型互不干扰。
        """
        profiles_json = self._serialize_profiles(profiles)
        async with AsyncSessionLocal() as cdb:
            existing = (
                await cdb.execute(
                    select(ContributorSyncCheckpoint).where(
                        ContributorSyncCheckpoint.repo_name == self.repo_name,
                        ContributorSyncCheckpoint.status == "active",
                        ContributorSyncCheckpoint.checkpoint_kind == "full_sync",
                    )
                )
            ).scalars().first()

            if existing:
                existing.last_processed_pr = last_processed_pr
                existing.total_prs = total_prs
                existing.since_date = since.date()
                existing.until_date = until.date()
                existing.profiles_snapshot = profiles_json
                existing.pr_numbers_done = sorted(pr_numbers_done)
                existing.contributors_count = len(profiles)
                existing.errors_count = errors_count
                existing.abort_reason = abort_reason[:500]
                existing.last_updated = datetime.now(UTC)
            else:
                cdb.add(ContributorSyncCheckpoint(
                    repo_name=self.repo_name,
                    checkpoint_kind="full_sync",
                    last_processed_pr=last_processed_pr,
                    total_prs=total_prs,
                    since_date=since.date(),
                    until_date=until.date(),
                    profiles_snapshot=profiles_json,
                    pr_numbers_done=sorted(pr_numbers_done),
                    contributors_count=len(profiles),
                    errors_count=errors_count,
                    abort_reason=abort_reason[:500],
                    status="active",
                    last_updated=datetime.now(UTC),
                ))
            await cdb.commit()
        logger.info(
            "Checkpoint saved: repo=%s last_pr=%d total=%d contributors=%d",
            self.repo_name, last_processed_pr, total_prs, len(profiles),
        )

    async def _load_checkpoint(
        self,
        since: datetime,
        until: datetime,
    ) -> tuple[dict[str, dict[str, Any]], set[int], int] | None:
        """加载匹配的 active checkpoint；窗口不匹配则清除旧 checkpoint。

        Returns:
            (profiles, pr_numbers_done, last_processed_pr) 或 None（无有效断点）
        """
        async with AsyncSessionLocal() as cdb:
            cp = (
                await cdb.execute(
                    select(ContributorSyncCheckpoint).where(
                        ContributorSyncCheckpoint.repo_name == self.repo_name,
                        ContributorSyncCheckpoint.status == "active",
                        ContributorSyncCheckpoint.checkpoint_kind == "full_sync",
                    )
                )
            ).scalars().first()
            if cp is None:
                return None

            # 校验时间窗口是否一致
            # until 允许 checkpoint 保存了原"至今"结束日期，新的"至今"请求可以续跑；
            # 但显式修改了结束日期时必须丢弃旧 checkpoint，避免窗口扩大后残留状态。
            cp_since: date = cp.since_date
            cp_until: date | None = cp.until_date
            since_d: date = since.date()
            until_d: date = until.date()
            if cp_since != since_d:
                logger.info(
                    "Checkpoint since mismatch (%s vs current %s), discarding",
                    cp_since, since_d,
                )
                cdb.delete(cp)
                await cdb.commit()
                return None
            if cp_until is not None and cp_until != until_d:
                logger.info(
                    "Checkpoint until mismatch (%s vs current %s), discarding",
                    cp_until, until_d,
                )
                cdb.delete(cp)
                await cdb.commit()
                return None

            profiles = self._deserialize_profiles(cp.profiles_snapshot)
            pr_done: set[int] = set(cp.pr_numbers_done or [])
            return profiles, pr_done, cp.last_processed_pr

    async def _clear_checkpoint(self) -> None:
        """同步完成后清除 active checkpoint（仅 full_sync 类型）。"""
        async with AsyncSessionLocal() as cdb:
            cps = (
                await cdb.execute(
                    select(ContributorSyncCheckpoint).where(
                        ContributorSyncCheckpoint.repo_name == self.repo_name,
                        ContributorSyncCheckpoint.status == "active",
                        ContributorSyncCheckpoint.checkpoint_kind == "full_sync",
                    )
                )
            ).scalars().all()
            for cp in cps:
                cdb.delete(cp)
            if cps:
                await cdb.commit()
                logger.info("Cleared %d active full_sync checkpoint(s) for %s", len(cps), self.repo_name)

    async def _finalize_daily_from_db(self) -> None:
        """从 daily_prs 表重建 _daily_pr_numbers / _daily_commit_shas，
        然后调用 _cleanup_stale_daily。用于跨窗口续跑：断点恢复后内存里的
        追踪集合是空的，需要从数据库里已有的行恢复，保证清理不会误删。"""
        result = await self.db.execute(
            select(DailyPR.pr_number, DailyPR.commits).where(
                DailyPR.project == self.repo_name
            )
        )
        self._daily_pr_numbers.clear()
        self._daily_commit_shas.clear()
        for row in result:
            self._daily_pr_numbers.add(row.pr_number)
            if row.commits:
                self._daily_commit_shas.update(row.commits)
        logger.info(
            "Rebuilt daily tracking from DB: prs=%d commits=%d",
            len(self._daily_pr_numbers), len(self._daily_commit_shas),
        )
        await self._cleanup_stale_daily()

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
        # 安全保护：0 个 profile 时不做任何 DB 变更，避免清空已有数据
        if not profiles:
            logger.warning(
                "_rebuild_profiles: 0 profiles for %s, skipping to preserve existing data",
                self.repo_name,
            )
            result.contributors_synced = 0
            return

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
    cancel_check_func=None,
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
                    collector = ContributorSyncCollector(
                        github_client, db,
                        progress_callback=repo_progress,
                        cancel_check_func=cancel_check_func,
                    )
                    # collect 不再自己提交，由外层统一 commit，实现 Build Plan + 画像原子化
                    result = await collector.collect(since=since_date, until=until_date, commit_profiles=False)
                    if result.total_prs_scanned == 0:
                        # 0 PR：可能是 token 失效 / 网络异常 / 日期范围无 PR，
                        # 此时 rebuild 已被内部跳过，不应 commit（保留旧数据）
                        logger.warning(
                            "Sync %s: 0 PRs fetched (likely API error or empty range), "
                            "skipping commit to preserve existing data",
                            repo_key,
                        )
                        try:
                            await db.rollback()
                        except Exception:
                            pass
                    elif result.errors and not result.contributors_synced:
                        # 被中断/完全失败/取消：直接回滚，保留上一份数据
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


async def sync_full_task_func(params: dict, tracker) -> dict:
    """全量贡献者同步的 BackgroundTaskTracker 任务函数。

    由 service.trigger_sync() 通过 BackgroundTaskTracker.create_and_run 调用，
    直接调用 sync_contributors 并同步更新 ContributorSyncRecord + background_task_records。
    """
    from infrastructure.db.base import SessionLocal as _SL
    from infrastructure.persistence.models import ContributorSyncRecord as _CSR

    record_id = params.get("sync_record_id")
    since_s = params.get("since_date")
    until_s = params.get("until_date")
    repo_name = params.get("repo_name")

    since_dt = datetime.fromisoformat(since_s) if since_s else None
    until_dt = datetime.fromisoformat(until_s) if until_s else None

    async def _progress(pct: int, msg: str):
        await tracker.update_status("running", msg, float(pct))
        if record_id:
            try:
                async with _SL() as db:
                    rec = await db.get(_CSR, record_id)
                    if rec:
                        rec.progress_percent = pct
                        rec.progress_message = msg
                        await db.commit()
            except Exception:
                pass

    async def _cancel_check() -> bool:
        try:
            async with _SL() as db:
                row = (
                    await db.execute(
                        text(
                            f"SELECT status FROM {BackgroundTaskTracker.TABLE_NAME} WHERE id = :tid"
                        ),
                        {"tid": tracker.task_id},
                    )
                ).fetchone()
            if row:
                return row[0] in ("cancelled", "failed")
        except Exception:
            pass
        return False

    try:
        result = await sync_contributors(
            since_date=since_dt, until_date=until_dt,
            repo_name=repo_name, progress_callback=_progress,
            cancel_check_func=_cancel_check,
        )
    except Exception as e:
        logger.error("sync_full_task_func failed: %s", e, exc_info=True)
        await tracker.fail(str(e))
        if record_id:
            try:
                async with _SL() as db:
                    rec = await db.get(_CSR, record_id)
                    if rec:
                        rec.status = "failed"
                        rec.errors = [f"Sync failed: {e}"]
                        rec.completed_at = datetime.now(UTC)
                        await db.commit()
            except Exception:
                pass
        return {"error": str(e)}

    # 更新 ContributorSyncRecord 为完成/部分成功
    if record_id:
        try:
            async with _SL() as db:
                rec = await db.get(_CSR, record_id)
                if rec and rec.status != "cancelled":
                    rec.status = "completed" if not result.errors else "failed"
                    rec.contributors_synced = result.contributors_synced
                    rec.commits_classified = result.commits_classified
                    rec.build_plans_created = result.build_plans_created
                    rec.total_prs_scanned = result.total_prs_scanned
                    rec.errors = result.errors or []
                    rec.duration_seconds = result.duration_seconds
                    rec.synced_at = result.synced_at
                    rec.completed_at = datetime.now(UTC)
                    rec.progress_percent = 100
                    await db.commit()
        except Exception:
            pass

    if not await tracker.is_cancelled():
        await tracker.complete(result_metadata={
            "contributors": result.contributors_synced,
            "prs": result.total_prs_scanned,
            "errors": len(result.errors),
        })
    return {
        "contributors_synced": result.contributors_synced,
        "total_prs": result.total_prs_scanned,
        "errors": len(result.errors),
    }


# ── 独立日度明细补录 ─────────────────────────────────────────────

async def backfill_daily_data(
    since_date: datetime | None = None,
    until_date: datetime | None = None,
    repo_name: str | None = None,
    progress_callback=None,
    cancel_check_func=None,
) -> SyncResult:
    """独立补录日度明细（daily_prs + daily_commits），不碰 contributor_profiles。

    用于：
    1. 全量同步时因限流跳过了日度写入，事后补录
    2. 日度数据丢失后重建
    3. 单独只刷新详情页图表数据

    需要 2 × PR 数 次 API 调用（commits + files），~16K PR ≈ ~5h
    （受 GitHub 5000/h 限额约束，RATE_LIMIT_WAIT_LOOPS=12 允许跨窗口等待）

    新增能力：
    - 每 DAILY_FLUSH_EVERY 个 PR 提交一次 DB（避免长事务 + 释放 session 资源）
    - 每 DAILY_CANCEL_CHECK_EVERY 个 PR 检查一次 cancel_check_func
    - 每 DAILY_CHECKPOINT_EVERY 个 PR 落一次 ContributorSyncCheckpoint
      (checkpoint_kind='daily_backfill')，cancel / 限流 / 崩溃后可断点续跑
    """

    token = settings.GITHUB_TOKEN
    if not token:
        logger.warning("GITHUB_TOKEN is not configured, skipping daily backfill")
        return SyncResult(errors=["GITHUB_TOKEN not configured"])

    if since_date is None:
        since_date = datetime(2025, 1, 1, tzinfo=UTC)
    if until_date is None:
        until_date = datetime.now(UTC)

    all_repos = _resolve_repos()
    if repo_name:
        if "/" in repo_name:
            target_repos = [repo_name.split("/", 1)]
        else:
            matched = [(o, r) for o, r in all_repos if r == repo_name]
            if not matched:
                return SyncResult(errors=[f"Repo not found: {repo_name}"])
            target_repos = matched
    else:
        target_repos = all_repos

    logger.info("Daily backfill target repos: %s since=%s until=%s",
                 target_repos, since_date.isoformat(), until_date.isoformat())

    start_time = datetime.now(UTC)
    combined = SyncResult()

    async def _load_backfill_checkpoint(repo_key: str) -> list[int] | None:
        """加载匹配 (repo, since_d, until_d) 的 active daily_backfill checkpoint。"""
        async with AsyncSessionLocal() as cdb:
            cp = (
                await cdb.execute(
                    select(ContributorSyncCheckpoint).where(
                        ContributorSyncCheckpoint.repo_name == repo_key,
                        ContributorSyncCheckpoint.status == "active",
                        ContributorSyncCheckpoint.checkpoint_kind == "daily_backfill",
                        ContributorSyncCheckpoint.since_date == since_date.date(),
                        ContributorSyncCheckpoint.until_date == until_date.date(),
                    )
                )
            ).scalars().first()
            if cp is None:
                return None
            return list(cp.pr_numbers_done or [])

    async def _save_backfill_checkpoint(
        repo_key: str, pr_numbers_done: set[int], last_pr: int, total: int,
        errors_count: int, reason: str,
    ) -> None:
        async with AsyncSessionLocal() as cdb:
            existing = (
                await cdb.execute(
                    select(ContributorSyncCheckpoint).where(
                        ContributorSyncCheckpoint.repo_name == repo_key,
                        ContributorSyncCheckpoint.status == "active",
                        ContributorSyncCheckpoint.checkpoint_kind == "daily_backfill",
                        ContributorSyncCheckpoint.since_date == since_date.date(),
                        ContributorSyncCheckpoint.until_date == until_date.date(),
                    )
                )
            ).scalars().first()
            if existing:
                existing.last_processed_pr = last_pr
                existing.total_prs = total
                existing.profiles_snapshot = None
                existing.pr_numbers_done = sorted(pr_numbers_done)
                existing.contributors_count = 0
                existing.errors_count = errors_count
                existing.abort_reason = reason[:500]
                existing.last_updated = datetime.now(UTC)
            else:
                cdb.add(ContributorSyncCheckpoint(
                    repo_name=repo_key,
                    checkpoint_kind="daily_backfill",
                    last_processed_pr=last_pr,
                    total_prs=total,
                    since_date=since_date.date(),
                    until_date=until_date.date(),
                    pr_numbers_done=sorted(pr_numbers_done),
                    contributors_count=0,
                    errors_count=errors_count,
                    abort_reason=reason[:500],
                    status="active",
                    last_updated=datetime.now(UTC),
                ))
            await cdb.commit()

    async def _clear_backfill_checkpoint(repo_key: str) -> None:
        async with AsyncSessionLocal() as cdb:
            cps = (
                await cdb.execute(
                    select(ContributorSyncCheckpoint).where(
                        ContributorSyncCheckpoint.repo_name == repo_key,
                        ContributorSyncCheckpoint.status == "active",
                        ContributorSyncCheckpoint.checkpoint_kind == "daily_backfill",
                        ContributorSyncCheckpoint.since_date == since_date.date(),
                        ContributorSyncCheckpoint.until_date == until_date.date(),
                    )
                )
            ).scalars().all()
            for cp in cps:
                cdb.delete(cp)
            if cps:
                await cdb.commit()
                logger.info("Cleared daily_backfill checkpoint for %s", repo_key)

    async def _backfill_single(owner: str, repo: str) -> SyncResult:
        repo_key = f"{owner}/{repo}"
        result = SyncResult()
        async with GitHubClient(token=token, owner=owner, repo=repo) as github:
            async with AsyncSessionLocal() as db:
                collector = ContributorSyncCollector(
                    github, db, progress_callback=None, cancel_check_func=cancel_check_func,
                )
                pr_numbers_done: set[int] = set()
                last_pr = 0
                cancelled = False
                try:
                    prs = await github.get_pull_requests_by_updated_range(
                        owner, repo, since_date, until_date, max_items=None
                    )
                    result.total_prs_scanned = len(prs)
                    logger.info("Backfill %s: fetched %d PRs", repo_key, len(prs))
                    if progress_callback:
                        await progress_callback(5, f"Fetched {len(prs)} PRs for daily backfill...")

                    total = len(prs)
                    done = 0
                    # 断点续跑：加载已完成集合
                    if total > 0:
                        prev_done = await _load_backfill_checkpoint(repo_key)
                        if prev_done:
                            pr_numbers_done.update(prev_done)
                            done = len(pr_numbers_done)
                            logger.info(
                                "Backfill %s: resuming, %d/%d already done",
                                repo_key, done, total,
                            )
                            if progress_callback:
                                await progress_callback(
                                    min(95, int(done / max(1, total) * 90) + 5),
                                    f"Resuming from checkpoint ({done}/{total} done)",
                                )

                    for idx, pr in enumerate(prs, 1):
                        pr_number = pr.get("number")
                        if pr_number is None:
                            continue

                        # 跳过断点已完成的
                        if pr_number in pr_numbers_done:
                            continue

                        # 周期检查取消
                        if cancel_check_func and idx % DAILY_CANCEL_CHECK_EVERY == 0:
                            try:
                                if await cancel_check_func():
                                    logger.info(
                                        "Backfill %s cancelled at PR #%s (%d/%d done)",
                                        repo_key, pr_number, done, total,
                                    )
                                    cancelled = True
                                    await db.rollback()
                                    await _save_backfill_checkpoint(
                                        repo_key, pr_numbers_done, last_pr,
                                        total, len(result.errors),
                                        f"cancelled at PR #{pr_number} after {done} done",
                                    )
                                    if progress_callback:
                                        await progress_callback(
                                            99, f"Cancelled at PR #{pr_number} ({done}/{total} done)",
                                        )
                                    result.errors.append(
                                        f"CANCELLED at PR #{pr_number} ({done}/{total} done)"
                                    )
                                    break
                            except Exception:
                                pass

                        if idx % 5 == 0 or idx == total:
                            pct = min(95, int(idx / max(1, total) * 90) + 5)
                            if progress_callback:
                                await progress_callback(pct, f"Backfill daily: {idx}/{total}")

                        user_info = pr.get("user") or {}
                        author = user_info.get("login") or ""
                        if not author:
                            pr_numbers_done.add(pr_number)
                            done += 1
                            continue

                        pr_created = collector._parse_datetime(pr.get("created_at"))
                        pr_merged_at = collector._parse_datetime(pr.get("merged_at"))
                        pr_state = pr.get("state", "closed")
                        is_merged = bool(pr.get("merged_at")) or (
                            pr_state == "closed" and bool(pr.get("merged"))
                        )

                        # 拉 commits + files（每个 PR 2 次 API）
                        commits: list[dict[str, Any]] = []
                        pr_files: list[dict[str, Any]] = []
                        try:
                            commits = await github.get_pr_commits(owner, repo, pr_number)
                        except GitHubRateLimitError:
                            raise
                        except Exception as e:
                            logger.warning("backfill commits PR #%s failed: %s", pr_number, e)
                        try:
                            pr_files = await github.get_pr_files(owner, repo, pr_number)
                        except GitHubRateLimitError:
                            raise
                        except Exception as e:
                            logger.warning("backfill files PR #%s failed: %s", pr_number, e)

                        # 写日度明细（幂等 upsert）
                        try:
                            await collector._write_daily_pr(
                                pr, pr_number, pr_state, is_merged,
                                pr_created, pr_merged_at, commits, [],
                            )
                        except Exception as e:
                            logger.warning("backfill daily PR #%s write skipped: %s", pr_number, e)
                        try:
                            await collector._write_daily_commits(
                                pr, pr_number, author, commits, pr_files, [],
                            )
                        except Exception as e:
                            logger.warning("backfill daily commits PR #%s write skipped: %s", pr_number, e)

                        result.commits_classified += len(commits)
                        pr_numbers_done.add(pr_number)
                        last_pr = pr_number
                        done += 1

                        # 周期性 commit + checkpoint
                        if done % DAILY_FLUSH_EVERY == 0:
                            try:
                                await db.commit()
                                logger.debug("Backfill %s: flush at %d/%d", repo_key, done, total)
                            except Exception as e:
                                logger.warning("Backfill %s: flush at %d failed: %s", repo_key, done, e)
                                try:
                                    await db.rollback()
                                except Exception:
                                    pass
                        if done % DAILY_CHECKPOINT_EVERY == 0:
                            try:
                                await _save_backfill_checkpoint(
                                    repo_key, pr_numbers_done, last_pr,
                                    total, len(result.errors),
                                    f"periodic at {done}/{total}",
                                )
                            except Exception as e:
                                logger.warning("Backfill %s: checkpoint save at %d failed: %s",
                                               repo_key, done, e)

                    # 完成后清除断点
                    if not cancelled:
                        await db.commit()
                        await _clear_backfill_checkpoint(repo_key)
                        logger.info(
                            "Backfill %s done: prs=%d commits=%d errors=%d (resumed=%d)",
                            repo_key, result.total_prs_scanned,
                            result.commits_classified, len(result.errors),
                            len(pr_numbers_done),
                        )
                    else:
                        result.contributors_synced = 0
                        # 已 append CANCELLED error
                    if result.commits_classified == 0 and not pr_numbers_done:
                        result.errors.append(f"{repo_key}: no PRs in range")

                except GitHubRateLimitError as e:
                    logger.error("Backfill %s interrupted by rate limit: %s", repo_key, e)
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                    await _save_backfill_checkpoint(
                        repo_key, pr_numbers_done, last_pr,
                        result.total_prs_scanned, len(result.errors),
                        f"rate limit: {e}",
                    )
                    result.errors.append(f"Rate limit: {e}")
                    if progress_callback:
                        await progress_callback(99, "Rate limited - re-trigger to resume")
                except Exception as e:
                    logger.error("Backfill %s failed: %s", repo_key, e, exc_info=True)
                    try:
                        await db.rollback()
                    except Exception:
                        pass
                    await _save_backfill_checkpoint(
                        repo_key, pr_numbers_done, last_pr,
                        result.total_prs_scanned, len(result.errors),
                        f"exception: {e}",
                    )
                    result.errors.append(f"Backfill failed: {e}")
        return result

    semaphore = asyncio.Semaphore(min(2, len(target_repos)))

    async def _with_sem(owner: str, repo: str) -> SyncResult:
        async with semaphore:
            return await _backfill_single(owner, repo)

    tasks = [_with_sem(o, r) for o, r in target_repos]
    repo_results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in repo_results:
        if isinstance(r, Exception):
            combined.errors.append(f"Backup error: {r}")
        else:
            combined.total_prs_scanned += r.total_prs_scanned
            combined.commits_classified += r.commits_classified
            combined.errors.extend(r.errors)
            if r.synced_at and (combined.synced_at is None or r.synced_at > combined.synced_at):
                combined.synced_at = r.synced_at

    if progress_callback:
        await progress_callback(100, "Daily backfill complete")

    combined.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()
    combined.synced_at = combined.synced_at or datetime.now(UTC)
    logger.info(
        "All daily backfill done: prs=%d commits=%d errors=%d duration=%.1fs",
        combined.total_prs_scanned, combined.commits_classified,
        len(combined.errors), combined.duration_seconds,
    )
    return combined


# ── BackgroundTaskTracker 兼容包装 ─────────────────────────────

async def backfill_daily_task_func(params: dict, tracker) -> None:
    """BackgroundTaskTracker 包装：执行 daily backfill 并同步更新 ContributorSyncRecord。

    签名匹配 BackgroundTaskTracker.start() 的 task_func 要求：
        async def task_func(params: dict, tracker: BackgroundTaskTracker)
    """
    from datetime import date as _date, datetime as _dt, UTC as _UTC
    from infrastructure.db.base import SessionLocal as _SL
    from infrastructure.persistence.models import ContributorSyncRecord as _CSR

    record_id = params.get("sync_record_id")
    since_s = params.get("since_date")
    until_s = params.get("until_date")
    repo_name = params.get("repo_name")

    since_dt = _dt.fromisoformat(since_s) if since_s else None
    until_dt = _dt.fromisoformat(until_s) if until_s else None

    async def _progress(pct: int, msg: str):
        # 更新 background_task_records（tracker）+ contributor_sync_records（业务）
        await tracker.update_status("running", msg, float(pct))
        if record_id:
            try:
                async with _SL() as db:
                    rec = await db.get(_CSR, record_id)
                    if rec:
                        rec.progress_percent = pct
                        rec.progress_message = msg
                        await db.commit()
            except Exception:
                pass

    async def _cancel_check():
        """检查 background task 是否被 cancel（供 backfill_daily_data 周期调用）。"""
        try:
            async with _SL() as db:
                row = (
                    await db.execute(
                        text(
                            f"SELECT status FROM {BackgroundTaskTracker.TABLE_NAME} WHERE id = :tid"
                        ),
                        {"tid": tracker.task_id},
                    )
                ).fetchone()
            if row:
                return row[0] in ("cancelled", "failed")
        except Exception:
            pass
        return False

    try:
        result = await backfill_daily_data(
            since_date=since_dt, until_date=until_dt,
            repo_name=repo_name, progress_callback=_progress,
            cancel_check_func=_cancel_check,
        )
    except Exception as e:
        logger.error("backfill_daily_task_func failed: %s", e, exc_info=True)
        await tracker.fail(str(e))
        if record_id:
            try:
                async with _SL() as db:
                    rec = await db.get(_CSR, record_id)
                    if rec:
                        rec.status = "failed"
                        rec.errors = [f"Daily backfill failed: {e}"]
                        rec.completed_at = _dt.now(_UTC)
                        await db.commit()
            except Exception:
                pass
        return

    # 更新 ContributorSyncRecord（cancelled 状态保留，不被覆盖）
    if record_id:
        try:
            async with _SL() as db:
                rec = await db.get(_CSR, record_id)
                if rec and rec.status not in ("cancelled", "failed"):
                    rec.status = "completed" if not result.errors else "failed"
                    rec.contributors_synced = 0
                    rec.commits_classified = result.commits_classified
                    rec.total_prs_scanned = result.total_prs_scanned
                    rec.errors = result.errors or []
                    rec.duration_seconds = result.duration_seconds
                    rec.synced_at = result.synced_at
                    rec.completed_at = _dt.now(_UTC)
                    rec.progress_percent = 100
                    await db.commit()
        except Exception:
            pass

    # 仅当任务未取消时才标记 background task 完成
    try:
        if tracker.is_cancelled():
            logger.info("backfill_daily_task_func %s skipped complete (cancelled)", tracker.task_id)
        else:
            await tracker.complete(result_metadata={
                "total_prs": result.total_prs_scanned,
                "commits": result.commits_classified,
                "errors": len(result.errors),
            })
    except Exception as e:
        logger.warning("backfill tracker.complete failed: %s", e)
