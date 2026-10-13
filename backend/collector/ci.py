"""
CI 数据采集服务
从 GitHub Actions API 采集 CI 运行数据并保存到数据库
"""
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubRateLimitError,
)
from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    CIJob,
    CIResult,
    DailyFailureRecord,
    JobFailureAnalysis,
    NightlyTestCase,
    WorkflowConfig,
)
from collector.npu_job_facts import record_sync_success, upsert_npu_job_fact
from infrastructure.tasks.sync_progress import SyncProgress, get_sync_progress, reset_sync_progress
from tooling.ci_version_snapshot import (
    PARSER_VERSION,
    get_version_snapshot,
    parse_ci_version_snapshot,
    version_snapshot_step_name,
)
from tooling.workflow_rules import matches_workflow_policy

logger = logging.getLogger(__name__)


class CIWorkflowCollectionError(RuntimeError):
    """Raised when a CI sync finishes with one or more workflows uncollected."""


class CICollector:
    """
    CI 数据采集服务

    功能：
    - 从 GitHub API 采集 workflow runs 数据
    - 识别硬件信息（A2/A3）
    - 保存到数据库
    """

    def __init__(
        self,
        github_client: GitHubClient,
        db_session: AsyncSession,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        sync_progress: SyncProgress | None = None,
    ):
        """
        初始化 CI 采集器

        Args:
            github_client: GitHub API 客户端
            db_session: 数据库会话
        """
        self.github = github_client
        self.db = db_session
        self.progress_callback = progress_callback
        self.sync_progress = sync_progress

    @staticmethod
    def _evidence_root() -> Path:
        """Return the immutable, run-scoped CI evidence root."""
        root = Path(settings.DATA_DIR)
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve() / "ci-evidence" / "runs"

    @staticmethod
    def _safe_artifact_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "artifact"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _extract_zip(archive: Path, destination: Path) -> None:
        """Extract a GitHub artifact without permitting Zip Slip paths."""
        destination_root = destination.resolve()
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                target = (destination / member.filename).resolve()
                try:
                    target.relative_to(destination_root)
                except ValueError as exc:
                    raise ValueError(f"Unsafe path in GitHub artifact: {member.filename}") from exc
            zf.extractall(destination)

    async def materialize_run_artifacts(self, run_id: int) -> None:
        """Cache immutable artifacts for one explicit workflow run.

        Failure analysis calls this on demand for the current failed run and
        the selected last-good run. Keeping artifact downloads out of CI sync
        lets workflow/job metadata commit promptly while analysis remains able
        to build two immutable, run-scoped evidence directories.
        """

        artifacts = await self.github.list_artifacts(run_id)
        if not artifacts:
            return

        run_dir = self._evidence_root() / str(run_id)
        artifact_dir = run_dir / "artifacts"
        extract_root = run_dir / "extracted"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {"run_id": run_id, "artifacts": {}}
        entries = manifest.setdefault("artifacts", {})

        for artifact in artifacts:
            artifact_id = int(artifact["id"])
            name = str(artifact.get("name") or f"artifact-{artifact_id}")
            safe_name = self._safe_artifact_name(name)
            archive = artifact_dir / f"{artifact_id}_{safe_name}.zip"
            expected_digest = str(artifact.get("digest") or "")
            valid = archive.exists() and zipfile.is_zipfile(archive)
            if valid and expected_digest.startswith("sha256:"):
                valid = self._sha256(archive) == expected_digest

            if not valid:
                try:
                    payload = await self.github.download_artifact(artifact_id)
                    with tempfile.NamedTemporaryFile(
                        dir=artifact_dir, prefix=f".{artifact_id}_", suffix=".part", delete=False
                    ) as temp:
                        temp.write(payload)
                        temporary_archive = Path(temp.name)
                    try:
                        if not zipfile.is_zipfile(temporary_archive):
                            raise ValueError("GitHub artifact response is not a ZIP archive")
                        actual_digest = self._sha256(temporary_archive)
                        if expected_digest.startswith("sha256:") and actual_digest != expected_digest:
                            raise ValueError("GitHub artifact SHA-256 does not match metadata")
                        os.replace(temporary_archive, archive)
                        valid = True
                    finally:
                        temporary_archive.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed to materialize artifact %s for run %s: %s", artifact_id, run_id, exc)
                    entries[str(artifact_id)] = {
                        "id": artifact_id, "name": name, "digest": expected_digest,
                        "state": "failed", "error": str(exc),
                    }
                    continue

            extraction_dir = extract_root / f"{artifact_id}_{safe_name}"
            if not extraction_dir.exists():
                temporary_dir = Path(tempfile.mkdtemp(dir=extract_root, prefix=f".{artifact_id}_"))
                try:
                    self._extract_zip(archive, temporary_dir)
                    os.replace(temporary_dir, extraction_dir)
                finally:
                    if temporary_dir.exists():
                        shutil.rmtree(temporary_dir, ignore_errors=True)
            entries[str(artifact_id)] = {
                "id": artifact_id, "name": name, "digest": expected_digest,
                "archive": str(archive.relative_to(run_dir)),
                "extracted": str(extraction_dir.relative_to(run_dir)), "state": "ready",
            }

        temporary_manifest = manifest_path.with_suffix(".json.part")
        temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary_manifest, manifest_path)

    @staticmethod
    def _is_pr_nightly_dispatch(
        run: dict[str, Any],
        jobs: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Identify a Nightly run dispatched by ``/nightly pr``."""
        if run.get("event") != "workflow_dispatch":
            return False

        input_sources: list[dict[str, Any]] = []
        for key in ("inputs", "workflow_dispatch_inputs", "dispatch_inputs"):
            value = run.get(key)
            if isinstance(value, dict):
                input_sources.append(value)
        workflow_dispatch = run.get("workflow_dispatch")
        if isinstance(workflow_dispatch, dict) and isinstance(workflow_dispatch.get("inputs"), dict):
            input_sources.append(workflow_dispatch["inputs"])

        for inputs in input_sources:
            if str(inputs.get("vllm_ascend_ref") or "").strip():
                return True
            if str(inputs.get("request_id") or "").strip():
                return True

        # The shared Nightly workflow exposes these conditional steps only when
        # ``vllm_ascend_ref``/``request_id`` is set by ``/nightly pr``.  The
        # workflow-run response does not include dispatch inputs, so inspect
        # the jobs response as a second source of truth.  Keep this list in
        # sync with the PR-only steps in the reusable Nightly workflows; a
        # successful or cancelled PR-only step is evidence that the run is a
        # PR run, while a skipped or not-yet-started step is present in normal
        # Nightly runs too.
        pr_only_step_markers = (
            "checkout pr code",
            "uninstall vlm vllm-ascend and remove code (if pr test)",
            "uninstall vllm and remove code (if pr test)",
            "checkout vllm-project/vllm-ascend repo",
            "move code to /vllm-workspace",
            "install vllm-project/vllm-ascend",
        )
        # Only inspect jobs that represent an actual test case. Aggregate
        # setup/build jobs can contain the same conditional step names or be
        # skipped for reasons unrelated to ``/nightly pr``.
        test_job_markers = ("single-node", "double-node", "multi-node")
        for job in jobs or []:
            job_name = str(job.get("name") or "").strip().casefold()
            if not any(marker in job_name for marker in test_job_markers):
                continue
            if job.get("conclusion") == "skipped":
                continue

            for step in job.get("steps") or []:
                name = str(step.get("name") or "").strip().casefold()
                if (
                    any(name == marker or name.startswith(f"{marker} ") for marker in pr_only_step_markers)
                    and step.get("conclusion") not in (None, "skipped")
                ):
                    return True

        return False

    async def _persist_progress(self, progress: Any) -> None:
        """Persist progress for callers running in a separate process."""
        if self.progress_callback is None:
            return
        try:
            await self.progress_callback(progress.get_progress())
        except Exception as exc:
            # Progress persistence must never stop an otherwise healthy sync.
            logger.warning("Failed to persist CI sync progress: %s", exc)

    async def _purge_pr_nightly_dispatch(self, run_id: int) -> int:
        """Remove a previously collected ``/nightly pr`` run and its derivatives."""
        deleted_rows = 0
        for model in (DailyFailureRecord, JobFailureAnalysis, CIJob, CIResult):
            result = await self.db.execute(delete(model).where(model.run_id == run_id))
            deleted_rows += int(getattr(result, "rowcount", 0) or 0)
        return deleted_rows

    async def collect_workflow_runs(
        self,
        workflow_files: list[str] | None = None,
        days_back: int = 7,
        max_runs_per_workflow: int = 100,
        force_full_refresh: bool = False,
    ) -> int:
        """
        采集 workflow 运行数据

        Args:
            workflow_files: workflow 文件名列表，None 则从数据库获取启用的配置
            days_back: 获取多少天的数据
            max_runs_per_workflow: 每个 workflow 最多获取多少条记录
            force_full_refresh: 是否强制全量覆盖刷新（忽略已有记录）

        Returns:
            新增或更新的记录数
        """
        # 如果没有指定 workflow 列表，从数据库获取启用且包含测试用例的配置。
        # CI 看板只关心真正执行测试的 workflow；纯构建、清理、发布等
        # workflow 不进入同步，避免无效 run/job 膨胀数据量。
        if workflow_files is None:
            test_workflow_names = select(NightlyTestCase.workflow_name).where(
                NightlyTestCase.enabled,
            ).distinct()
            stmt = select(WorkflowConfig).where(
                WorkflowConfig.enabled,
                WorkflowConfig.workflow_name.in_(test_workflow_names),
            )
            result = await self.db.execute(stmt)
            workflow_configs = result.scalars().all()
            workflow_files = [
                (
                    config.workflow_file, config.hardware, config.event or None,
                    config.actor, config.stats_start_hour, config.stats_end_hour,
                    config.materialize_name_regex,
                )
                for config in workflow_configs
            ]
            logger.info(
                "Loaded %d enabled workflows with test cases from database",
                len(workflow_files),
            )
        else:
            # 即使调用方显式传入 workflow，也要应用相同的测试用例规则。
            # 这样手动同步、定时同步和历史调用路径不会产生不同的数据集。
            test_workflow_files = select(WorkflowConfig).where(
                WorkflowConfig.enabled,
                WorkflowConfig.workflow_file.in_(workflow_files),
                WorkflowConfig.workflow_name.in_(
                    select(NightlyTestCase.workflow_name).where(
                        NightlyTestCase.enabled,
                    ).distinct()
                ),
            )
            result = await self.db.execute(test_workflow_files)
            configs_by_file = {
                config.workflow_file: config for config in result.scalars().all()
            }
            skipped_workflows = [
                name for name in workflow_files if name not in configs_by_file
            ]
            workflow_files = [
                (
                    config.workflow_file, config.hardware, config.event or None,
                    config.actor, config.stats_start_hour, config.stats_end_hour,
                    config.materialize_name_regex,
                )
                for name in workflow_files
                if (config := configs_by_file.get(name)) is not None
            ]
            if skipped_workflows:
                logger.info(
                    "Skipped %d workflows without enabled test cases: %s",
                    len(skipped_workflows),
                    ", ".join(skipped_workflows),
                )

        # 初始化进度跟踪器
        if self.sync_progress is None:
            reset_sync_progress()
            progress = get_sync_progress()
        else:
            progress = self.sync_progress
            progress.total_workflows = len(workflow_files)
        # 如果还没有开始（API 层可能已经启动了），则启动
        if progress.status == "idle":
            progress.total_workflows = len(workflow_files)
            progress.start()
        else:
            # 更新 workflow 数量
            progress.total_workflows = len(workflow_files)

        await self._persist_progress(progress)

        since = datetime.now(UTC) - timedelta(days=days_back)
        total_collected = 0

        logger.info(f"Starting CI data collection since {since.isoformat()}, force_full_refresh={force_full_refresh}")

        for wf_item in workflow_files:
            workflow_file, hardware = wf_item[0], wf_item[1]
            actor_filter = wf_item[3] if len(wf_item) > 3 else None
            start_hour = wf_item[4]
            end_hour = wf_item[5]
            name_regex = wf_item[6]
            try:
                # 更新当前正在处理的 workflow
                progress.current_workflow = workflow_file
                progress.workflow_details[workflow_file] = {
                    "status": "running",
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                await self._persist_progress(progress)

                collected = await self._collect_single_workflow(
                    workflow_file=workflow_file,
                    since=since,
                    progress=progress,
                    max_runs=max_runs_per_workflow,
                    hardware=hardware,
                    force_full_refresh=force_full_refresh,
                    event=None,
                    actor=actor_filter,
                    stats_start_hour=start_hour,
                    stats_end_hour=end_hour,
                    materialize_name_regex=name_regex,
                )
                total_collected += collected

                # 更新进度
                progress.update_workflow_progress(workflow_file, collected, "completed")
                await self._persist_progress(progress)
                logger.info(f"Collected {collected} runs for {workflow_file}")

            except GitHubRateLimitError as e:
                logger.error(f"Rate limit exceeded while fetching {workflow_file}: {e}")
                progress.update_workflow_progress(workflow_file, 0, "failed", str(e))
                progress.fail(f"Failed to collect {workflow_file}: {e}")
                await self._persist_progress(progress)
                try:
                    await self.db.rollback()
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback database transaction: {rollback_error}")
                raise
            except GitHubAuthenticationError as e:
                logger.error(f"GitHub authentication failed while fetching {workflow_file}: {e}")
                progress.update_workflow_progress(workflow_file, 0, "failed", str(e))
                progress.fail(f"Failed to collect {workflow_file}: {e}")
                await self._persist_progress(progress)
                await self.db.rollback()
                raise
            except GitHubAPIError as e:
                logger.error(f"Failed to fetch workflow {workflow_file}: {e}")
                progress.update_workflow_progress(workflow_file, 0, "failed", str(e))
                await self._persist_progress(progress)
                try:
                    await self.db.rollback()
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback database transaction: {rollback_error}")
                continue  # 继续尝试其他 workflow
            except Exception as e:
                logger.error(f"Unexpected error processing {workflow_file}: {e}", exc_info=True)
                progress.update_workflow_progress(workflow_file, 0, "failed", str(e))
                await self._persist_progress(progress)
                try:
                    await self.db.rollback()
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback database transaction: {rollback_error}")
                continue

        failed_workflows = [
            name
            for name, details in progress.workflow_details.items()
            if details.get("status") == "failed"
        ]
        if failed_workflows:
            message = "Failed workflows: " + ", ".join(failed_workflows)
            progress.fail(message)
            await self._persist_progress(progress)
            raise CIWorkflowCollectionError(message)

        # 完成同步
        progress.complete()
        await self._persist_progress(progress)
        logger.info(f"CI data collection completed. Total: {total_collected} runs")
        return total_collected

    async def _collect_single_workflow(
        self,
        workflow_file: str,
        since: datetime,
        progress: Any,
        max_runs: int = 100,
        hardware: str = "A2",
        force_full_refresh: bool = False,
        event: str | None = None,
        actor: str | None = None,
        stats_start_hour: int | None = None,
        stats_end_hour: int | None = None,
        materialize_name_regex: str | None = None,
    ) -> int:
        """
        采集单个 workflow 的运行数据

        Args:
            workflow_file: workflow 文件名
            since: 起始时间
            progress: 进度跟踪器
            max_runs: 最多获取多少条记录
            hardware: 硬件类型
            force_full_refresh: 是否强制全量覆盖刷新（忽略已有记录）
            event: 事件类型过滤（None=不过滤）
            actor: 触发人过滤（None=不过滤）

        Returns:
            新增或更新的记录数
        """
        collected = 0
        updated_count = 0
        page = 1

        # 获取该 workflow 已同步的最新 run_id（用于增量同步）
        last_synced_run_id = await self._get_last_synced_run_id(workflow_file)
        if last_synced_run_id and not force_full_refresh:
            logger.info(f"Last synced run_id for {workflow_file}: {last_synced_run_id}, will use incremental sync")

        logger.info(f"Collecting workflow: {workflow_file}, since: {since.isoformat()}, force_full_refresh={force_full_refresh}")

        while True:
            try:
                # GitHub can label schedule-triggered reusable workflows as
                # workflow_dispatch.  The configured event is therefore not a
                # reliable API filter; materialization rules below decide which
                # runs belong on the dashboard.
                # 使用 created 参数进行时间过滤（无论增量还是全量模式）
                # 全量模式下也需要限制时间范围，避免获取过多历史数据
                since_date = since.strftime('%Y-%m-%d')
                created_filter = f">={since_date}"

                runs = await self.github.get_workflow_runs(
                    workflow_id_or_name=workflow_file,
                    event=None,
                    per_page=min(max_runs, 100),
                    page=page,
                    created=created_filter,  # 使用 created 参数过滤
                )
            except (GitHubAuthenticationError, GitHubRateLimitError):
                # Credential and quota failures must reach the durable task
                # worker; swallowing them would mark a zero-row sync done.
                raise
            except Exception as e:
                logger.error(f"Failed to fetch workflow runs for {workflow_file} (page {page}): {e}")
                break

            raw_count = len(runs)

            # actor 客户端过滤（GitHub API 不支持 actor 参数）
            if actor and runs:
                runs = [r for r in runs if (r.get("actor") or {}).get("login") == actor]

            logger.info(f"Fetched {len(runs)}/{raw_count} runs for {workflow_file} (page {page}, event={event or 'all'}, actor={actor or 'all'})")

            if not runs and raw_count < min(max_runs, 100):
                # Only break if API returned less than a full page AND filtered result is empty
                break  # 真正没有更多数据
            if not runs:
                # Filtered to zero but API returned a full page — keep going
                page += 1
                continue

            # 处理每条记录
            for run in runs:
                created_at = self._parse_datetime(run.get("created_at"))
                run_id = run.get("id")

                if created_at and created_at < since:
                    logger.info(f"Run {run_id} is older than {since}, stopping collection for {workflow_file}")
                    await self.db.commit()
                    return collected

                if not matches_workflow_policy(
                    created_at,
                    str(run.get("name") or "").strip(),
                    stats_start_hour,
                    stats_end_hour,
                    materialize_name_regex,
                ):
                    logger.debug("Run %s excluded by configured workflow conditions", run_id)
                    continue

                prefetched_jobs: list[dict[str, Any]] | None = None
                if run.get("event") == "workflow_dispatch":
                    try:
                        prefetched_jobs = await self.github.get_job_list(run_id)
                    except (GitHubAuthenticationError, GitHubRateLimitError):
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Unable to inspect jobs for workflow dispatch run %s: %s",
                            run_id,
                            exc,
                        )

                if self._is_pr_nightly_dispatch(run, prefetched_jobs):
                    deleted_rows = await self._purge_pr_nightly_dispatch(run_id)
                    logger.info(
                        "Skipped /nightly pr workflow dispatch run %s for %s; "
                        "removed %d previously collected rows",
                        run_id,
                        workflow_file,
                        deleted_rows,
                    )
                    continue

                # 保存或更新记录
                updated = await self._save_ci_result(run, workflow_file, hardware)
                # Flush the parent before collecting children. A failed insert
                # must abort this run instead of leaving Jobs without a
                # persisted CIResult (or giving them a fallback workflow name).
                await self.db.flush()
                if updated:
                    collected += 1
                    updated_count += 1
                    # 实时更新已采集记录数
                    progress.update_collected_count(1)
                elif not force_full_refresh:
                    logger.debug(f"Run {run_id} exists and is unchanged")
                else:
                    # 强制刷新模式下，即使已存在也要更新
                    logger.debug(f"Run {run_id} exists, but force refresh enabled")

                # 获取并保存 job 详细信息（无论 run 是否已存在，都尝试获取 jobs）
                try:
                    # 在强制刷新模式下，也强制更新 runner 信息
                    await self._collect_jobs(
                        run_id,
                        run,
                        workflow_file,
                        hardware,
                        force_update_runner=force_full_refresh,
                        jobs=prefetched_jobs,
                    )
                except (GitHubAuthenticationError, GitHubRateLimitError):
                    raise
                except Exception as e:
                    logger.error(f"Failed to collect jobs for run {run_id}: {e}")

            # 检查是否达到最大数量
            if raw_count < min(max_runs, 100):
                logger.info(f"Reached last page for {workflow_file}")
                break  # 已经是最后一页

            page += 1

            # 防止过度分页
            if page > 10:
                logger.warning(f"Reached max pagination for {workflow_file}")
                break

        # 提交最后的数据
        await self.db.commit()
        logger.info(f"Collection completed for {workflow_file}: {collected} new/updated, {updated_count} updated")
        return collected

    async def _save_ci_result(
        self,
        run: dict[str, Any],
        workflow_file: str,
        hardware: str,
    ) -> bool:
        """
        保存或更新 CI 结果

        Args:
            run: GitHub API 返回的 run 数据
            workflow_file: workflow 文件名

        Returns:
            True if the parent was created or updated; False only if an
            existing parent was unchanged. Save failures raise instead.
        """
        run_id = run.get("id")
        if not run_id:
            raise ValueError("Workflow run has no ID; cannot collect its Jobs")

        # 检查是否已存在
        stmt = select(CIResult).where(CIResult.run_id == run_id)
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # 更新现有记录
            return await self._update_ci_result(existing, run, hardware)
        else:
            # 创建新记录
            return await self._create_ci_result(run, workflow_file, hardware)

    async def _create_ci_result(
        self,
        run: dict[str, Any],
        workflow_file: str,
        hardware: str,
    ) -> bool:
        """创建新的 CI 结果记录"""
        try:
            # 从 WorkflowConfig 获取正确的 workflow_name
            from sqlalchemy import select

            from infrastructure.persistence.models import WorkflowConfig

            stmt = select(WorkflowConfig.workflow_name).where(
                WorkflowConfig.workflow_file == workflow_file
            )
            result = await self.db.execute(stmt)
            config_workflow_name = result.scalar_one_or_none()

            # 如果找不到配置，使用 GitHub API 返回的名称
            if config_workflow_name is None:
                logger.warning(f"WorkflowConfig not found for {workflow_file}, using GitHub name")
                config_workflow_name = run.get("name", workflow_file)

            ci_result = CIResult(
                workflow_name=config_workflow_name,
                run_id=run["id"],
                run_number=run.get("run_number"),
                status=run.get("status", "unknown"),
                conclusion=run.get("conclusion"),
                event=run.get("event"),
                branch=run.get("head_branch"),
                head_sha=run.get("head_sha"),
                started_at=self._parse_datetime(run.get("created_at")),
                completed_at=self._parse_datetime(run.get("updated_at")),
                duration_seconds=self._calculate_duration(run),
                hardware=hardware,
                data=json.dumps(run),
            )

            self.db.add(ci_result)
            # 不立即 commit，由上层统一 commit
            logger.debug(f"Created CI result for run {run['id']}")
            return True

        except Exception as e:
            logger.exception("Failed to create CI result for run %s", run.get("id"))
            raise RuntimeError(f"Failed to create CI result for run {run.get('id')}") from e

    async def _update_ci_result(
        self,
        existing: CIResult,
        run: dict[str, Any],
        hardware: str,
    ) -> bool:
        """更新现有 CI 结果记录"""
        try:
            # 只更新状态变化的字段
            needs_update = False

            new_status = run.get("status", "unknown")
            if existing.status != new_status:
                existing.status = new_status
                needs_update = True

            new_conclusion = run.get("conclusion")
            if existing.conclusion != new_conclusion:
                existing.conclusion = new_conclusion
                needs_update = True

            # 更新 run_number（如果之前为空）
            new_run_number = run.get("run_number")
            if existing.run_number is None and new_run_number is not None:
                existing.run_number = new_run_number
                needs_update = True

            # 更新 event（如果之前为空）
            new_event = run.get("event")
            if existing.event is None and new_event is not None:
                existing.event = new_event
                needs_update = True

            # 更新 branch（如果之前为空）
            new_branch = run.get("head_branch")
            if existing.branch is None and new_branch is not None:
                existing.branch = new_branch
                needs_update = True

            # 更新 head_sha（如果之前为空）
            new_head_sha = run.get("head_sha")
            if existing.head_sha is None and new_head_sha is not None:
                existing.head_sha = new_head_sha
                needs_update = True

            new_completed_at = self._parse_datetime(run.get("updated_at"))
            if existing.completed_at != new_completed_at and new_completed_at is not None:
                existing.completed_at = new_completed_at
                needs_update = True

            new_duration = self._calculate_duration(run)
            if existing.duration_seconds != new_duration and new_duration is not None:
                existing.duration_seconds = new_duration
                needs_update = True

            # 更新硬件信息（如果之前识别错误）
            if existing.hardware != hardware and hardware != "unknown":
                existing.hardware = hardware
                needs_update = True

            # 更新原始数据
            previous_data = json.loads(existing.data) if existing.data else {}
            if isinstance(previous_data, dict) and previous_data.get("dashboard_evidence"):
                run = dict(run)
                run["dashboard_evidence"] = previous_data["dashboard_evidence"]
            existing.data = json.dumps(run)
            needs_update = True

            if needs_update:
                # 不立即 commit，由上层统一 commit
                logger.debug(f"Updated CI result for run {run['id']}")

            return needs_update

        except Exception as e:
            logger.exception("Failed to update CI result for run %s", run.get("id"))
            raise RuntimeError(f"Failed to update CI result for run {run.get('id')}") from e

    def _calculate_duration(self, run: dict[str, Any]) -> int | None:
        """
        计算运行时长（秒）

        started_at 优先使用 GitHub 的 run_started_at（实际执行开始），
        排除排队等待时间；completed_at 使用 updated_at（GitHub API
        无 run_updated_at 字段，updated_at 即为运行结束时间）。

        Args:
            run: workflow run 数据

        Returns:
            时长（秒），无法计算时返回 None
        """
        started_at = self._parse_datetime(run.get("run_started_at") or run.get("created_at"))
        completed_at = self._parse_datetime(run.get("updated_at"))

        if started_at and completed_at:
            return int((completed_at - started_at).total_seconds())

        return None

    def _parse_datetime(self, dt_string: str | None) -> datetime | None:
        """
        解析 ISO 8601 格式的日期时间字符串

        Args:
            dt_string: 日期时间字符串，如 "2024-03-24T10:00:00Z"

        Returns:
            datetime 对象，解析失败时返回 None
        """
        if not dt_string:
            return None

        try:
            # 处理 Z 后缀
            dt_string = dt_string.replace("Z", "+00:00")
            return datetime.fromisoformat(dt_string)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse datetime '{dt_string}': {e}")
            return None

    async def _collect_jobs(
        self,
        run_id: int,
        run_data: dict[str, Any],
        workflow_file: str,
        hardware: str,
        force_update_runner: bool = False,
        jobs: list[dict[str, Any]] | None = None,
    ) -> int:
        """
        获取并保存 workflow run 的 job 详细信息

        Args:
            run_id: workflow run ID
            run_data: workflow run 数据
            workflow_file: workflow 文件名
            hardware: 硬件类型
            force_update_runner: 是否强制更新 runner 信息

        Returns:
            采集的 job 数量
        """
        try:
            logger.info(f"Fetching jobs for run {run_id}...")

            # 获取 job 列表；调用方已预取时不重复请求 GitHub。
            if jobs is None:
                jobs = await self.github.get_job_list(run_id)
            logger.info(f"GitHub API returned {len(jobs)} jobs for run {run_id}")

            if not jobs:
                logger.warning(f"No jobs found for run {run_id}")
                return 0

            # CIResult is created from WorkflowConfig and therefore owns the
            # stable dashboard identity for this run. Do not use the
            # workflow file name or GitHub's decorated Job display name below.
            result = await self.db.execute(
                select(CIResult.workflow_name).where(CIResult.run_id == run_id)
            )
            configured_workflow_name = result.scalar_one_or_none()
            if configured_workflow_name is None:
                raise RuntimeError(
                    f"Cannot collect Jobs for run {run_id}: parent CIResult is missing"
                )

            saved_count = 0
            new_count = 0
            update_count = 0

            for job in jobs:
                job_id = job.get("id")
                if not job_id:
                    logger.warning(f"Job missing ID in run {run_id}")
                    continue

                # 检查是否已存在
                stmt = select(CIJob).where(CIJob.job_id == job_id)
                result = await self.db.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # 更新现有记录
                    updated = await self._update_ci_job(
                        existing,
                        job,
                        configured_workflow_name,
                        hardware,
                        force_update_runner,
                    )
                    if updated:
                        update_count += 1
                    saved_count += 1
                else:
                    # 创建新记录
                    created = await self._create_ci_job(
                        job, run_id, configured_workflow_name, hardware
                    )
                    if created:
                        new_count += 1
                        saved_count += 1

                await self._sync_npu_job_fact(job, run_data, configured_workflow_name)

                # 如果 job 失败且已完成，自动获取日志（已禁用，直接跳转到 GitHub 查看）
                # job_conclusion = job.get("conclusion")
                # job_status = job.get("status")
                # if job_status == "completed" and job_conclusion == "failure":
                #     try:
                #         logs = await self.github.get_job_logs(job_id)
                #         if logs:
                #             # 更新 job 记录，保存日志
                #             await self._save_job_logs(job_id, logs)
                #             failed_jobs_with_logs += 1
                #             logger.info(f"Auto-fetched logs for failed job {job_id}")
                #     except Exception as e:
                #         logger.error(f"Failed to fetch logs for job {job_id}: {e}")

            logger.info(f"Collected {saved_count} jobs for run {run_id} (new: {new_count}, updated: {update_count})")

            # A completed version-probe Job supplies the actual checkout for the
            # run. Failure here is intentionally non-fatal so ordinary CI
            # collection can still complete.
            await self._collect_run_version_snapshot(run_id, jobs)

            # 注意：不在这里 commit，由外层统一 commit
            return saved_count

        except Exception as e:
            logger.error(f"Failed to collect jobs for run {run_id}: {e}", exc_info=True)
            raise  # 抛出异常，由外层处理回滚

    async def _sync_npu_job_fact(
        self,
        job: dict[str, Any],
        run_data: dict[str, Any],
        workflow_name: str,
    ) -> None:
        """Persist the analytical fact alongside the existing raw CI job."""
        owner = str(getattr(self.github, "owner", ""))
        repo = str(getattr(self.github, "repo", ""))
        repository = f"{owner}/{repo}".strip("/") or "unknown/unknown"
        fact = await upsert_npu_job_fact(
            self.db,
            job,
            run_data,
            repository=repository,
            workflow_name=workflow_name,
        )
        await record_sync_success(
            self.db,
            repository=repository,
            job_id=fact.job_id,
            watermark=fact.source_updated_at,
        )

    async def _collect_run_version_snapshot(
        self, run_id: int, jobs: list[dict[str, Any]], *, force: bool = False
    ) -> dict[str, Any] | None:
        result = await self.db.execute(select(CIResult).where(CIResult.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            return None
        existing = get_version_snapshot(run.data)
        if not force and existing.get("status") == "complete" and existing.get("parser_version") == PARSER_VERSION:
            return existing

        candidates = [
            (job, version_snapshot_step_name(job.get("steps")))
            for job in jobs
            if job.get("status") == "completed"
        ]
        for job, source_step in candidates:
            if not source_step:
                continue
            try:
                logs = await self.github.get_job_logs(int(job["id"]))
                snapshot = parse_ci_version_snapshot(
                    logs, source_job_id=int(job["id"]), source_step=source_step
                )
                if not snapshot:
                    continue
                snapshot["collected_at"] = datetime.now(UTC).isoformat()
                payload = json.loads(run.data) if run.data else {}
                if not isinstance(payload, dict):
                    payload = {}
                payload.setdefault("dashboard_evidence", {})["version_snapshot"] = snapshot
                run.data = json.dumps(payload)
                logger.info("Collected version snapshot for run %s from job %s", run_id, job["id"])
                return snapshot
            except Exception as exc:
                logger.warning(
                    "Version snapshot probe failed for run %s job %s: %s",
                    run_id, job.get("id"), exc,
                )
        return None

    async def _create_ci_job(
        self,
        job: dict[str, Any],
        run_id: int,
        configured_workflow_name: str,
        hardware: str,
    ) -> bool:
        """创建新的 CI Job 记录"""
        try:
            # 提取 steps 信息
            steps = job.get("steps", [])
            steps_summary = []
            for step in steps:
                steps_summary.append({
                    "name": step.get("name", ""),
                    "status": step.get("status", ""),
                    "conclusion": step.get("conclusion"),
                    "number": step.get("number", 0),
                })

            # 提取 runner 标签（GitHub API 直接在 job 顶层返回 labels 字段）
            runner_labels = job.get("labels", [])
            if isinstance(runner_labels, list):
                # 标签可能是字典列表或字符串列表
                label_names = []
                for label in runner_labels:
                    if isinstance(label, dict):
                        label_names.append(label.get("name", ""))
                    else:
                        label_names.append(str(label))
                runner_labels = label_names

            ci_job = CIJob(
                job_id=job["id"],
                run_id=run_id,
                workflow_name=self._match_configured_workflow_name(
                    job.get("workflow_name"), configured_workflow_name
                ),
                job_name=job.get("name", ""),
                status=job.get("status", "unknown"),
                conclusion=job.get("conclusion"),
                started_at=self._parse_datetime(job.get("started_at")),
                completed_at=self._parse_datetime(job.get("completed_at")),
                duration_seconds=self._calculate_job_duration(job),
                hardware=hardware,
                runner_name=job.get("runner_name"),  # GitHub API 直接返回 runner_name 字段
                runner_labels=json.dumps(runner_labels),
                steps_data=json.dumps(steps_summary),
                logs_url=f"https://api.github.com/repos/{self.github.owner}/{self.github.repo}/actions/jobs/{job['id']}/logs",
                data=json.dumps(job),
            )

            self.db.add(ci_job)
            logger.debug(f"Created CI job {job['id']} for run {run_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to create CI job: {e}")
            return False

    @staticmethod
    def _match_configured_workflow_name(
        github_workflow_name: Any, configured_workflow_name: str,
    ) -> str:
        """Map a GitHub display name to its configured workflow identity.

        GitHub's Job payload can append runtime context such as ``(PR) 14544``
        or ``(scheduled)``. A collector invocation is already scoped to one
        configured workflow, so its configured name remains the safe canonical
        value even when GitHub returns a decorated display name.
        """
        raw_name = str(github_workflow_name or "").strip()
        canonical_name = configured_workflow_name.strip()
        if not raw_name or raw_name.casefold() == canonical_name.casefold():
            return canonical_name

        suffix = raw_name[len(canonical_name):].lstrip() if raw_name.casefold().startswith(
            canonical_name.casefold()
        ) else ""
        if suffix.startswith("("):
            logger.info(
                "Normalized GitHub workflow display name %r to configured name %r",
                raw_name,
                canonical_name,
            )
        else:
            logger.warning(
                "GitHub Job workflow name %r does not match configured workflow %r; "
                "using configured name because collection is workflow-scoped",
                raw_name,
                canonical_name,
            )
        return canonical_name

    async def _update_ci_job(
        self,
        existing: CIJob,
        job: dict[str, Any],
        configured_workflow_name: str,
        hardware: str,
        force_update_runner: bool = False,
    ) -> bool:
        """更新现有 CI Job 记录

        Args:
            existing: 现有的 CIJob 记录
            job: GitHub API 返回的 job 数据
            workflow_file: workflow 文件名
            hardware: 硬件类型
            force_update_runner: 是否强制更新 runner 信息
        """
        try:
            needs_update = False

            canonical_workflow_name = self._match_configured_workflow_name(
                job.get("workflow_name"), configured_workflow_name
            )
            if existing.workflow_name != canonical_workflow_name:
                existing.workflow_name = canonical_workflow_name
                needs_update = True

            # 更新状态
            new_status = job.get("status", "unknown")
            if existing.status != new_status:
                existing.status = new_status
                needs_update = True

            new_conclusion = job.get("conclusion")
            if existing.conclusion != new_conclusion:
                existing.conclusion = new_conclusion
                needs_update = True

            # 更新时间
            new_completed_at = self._parse_datetime(job.get("completed_at"))
            if new_completed_at and existing.completed_at != new_completed_at:
                existing.completed_at = new_completed_at
                needs_update = True

                # 重新计算时长
                new_duration = self._calculate_job_duration(job)
                if new_duration and existing.duration_seconds != new_duration:
                    existing.duration_seconds = new_duration
                    needs_update = True

            # 更新 steps 信息
            steps = job.get("steps", [])
            if steps:
                steps_summary = []
                for step in steps:
                    steps_summary.append({
                        "name": step.get("name", ""),
                        "status": step.get("status", ""),
                        "conclusion": step.get("conclusion"),
                        "number": step.get("number", 0),
                    })
                existing.steps_data = json.dumps(steps_summary)
                needs_update = True

            # 更新 runner 信息（GitHub API 直接在 job 顶层返回 runner_name 和 labels 字段）
            new_runner_name = job.get("runner_name")
            if force_update_runner and new_runner_name:
                # 强制更新模式：总是更新 runner 信息
                if existing.runner_name != new_runner_name:
                    existing.runner_name = new_runner_name
                    needs_update = True
            elif new_runner_name and existing.runner_name != new_runner_name:
                # 普通模式：只在 runner 名称变化时更新
                existing.runner_name = new_runner_name
                needs_update = True

            new_runner_labels = job.get("labels", [])
            if isinstance(new_runner_labels, list):
                label_names = [
                    label.get("name", "") if isinstance(label, dict) else str(label)
                    for label in new_runner_labels
                ]
                new_runner_labels_str = json.dumps(label_names)
                if existing.runner_labels != new_runner_labels_str:
                    existing.runner_labels = new_runner_labels_str
                    needs_update = True

            # 保留 Dashboard 生成的证据。性能精度日志解析结果缓存在此处，
            # 避免用户每次查看历史趋势都重新下载同一份 GitHub 日志。
            previous_data = json.loads(existing.data) if existing.data else {}
            if isinstance(previous_data, dict) and previous_data.get("dashboard_evidence"):
                job = dict(job)
                job["dashboard_evidence"] = previous_data["dashboard_evidence"]
            existing.data = json.dumps(job)
            needs_update = True

            if needs_update:
                logger.debug(f"Updated CI job {job['id']}")

            return needs_update

        except Exception as e:
            logger.error(f"Failed to update CI job: {e}")
            return False

    def _calculate_job_duration(self, job: dict[str, Any]) -> int | None:
        """计算 job 运行时长（秒）"""
        started_at = self._parse_datetime(job.get("started_at"))
        completed_at = self._parse_datetime(job.get("completed_at"))

        if started_at and completed_at:
            return int((completed_at - started_at).total_seconds())

        return None

    async def _get_last_synced_run_id(self, workflow_file: str) -> int | None:
        try:
            stmt = select(WorkflowConfig.workflow_name).where(
                WorkflowConfig.workflow_file == workflow_file
            )
            config_result = await self.db.execute(stmt)
            workflow_name = config_result.scalar_one_or_none()

            if not workflow_name:
                logger.warning(f"WorkflowConfig not found for {workflow_file}, using workflow_file as name")
                workflow_name = workflow_file

            stmt = select(func.max(CIResult.run_id)).where(CIResult.workflow_name == workflow_name)
            result = await self.db.execute(stmt)
            max_run_id = result.scalar()
            return max_run_id if max_run_id else None
        except Exception as e:
            logger.warning(f"Failed to get last synced run_id for {workflow_file}: {e}")
            return None
