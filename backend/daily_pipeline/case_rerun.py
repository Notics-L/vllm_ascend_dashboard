"""Rerun an environment-classified Daily test job in its original workflow."""
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from daily_pipeline.collection_source import execution_result
from infrastructure.clients.daily_pipeline_collection import (
    CollectionSourceError,
    DailyCollectionClient,
)
from infrastructure.clients.daily_pipeline_scheduler import SchedulerUpdateError, start_collection
from infrastructure.core.github_credentials import daily_github_credential
from infrastructure.persistence.daily_pipeline_collection import DailyCollectionRepository
from infrastructure.persistence.daily_pipeline_settings import read_settings
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE
from infrastructure.persistence.models import DailyPipelineCollectionNode as Node
from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineFailure as Failure

logger = logging.getLogger(__name__)


def can_rerun_case(failure: Failure, node: Node, run: Run) -> bool:
    return (
        failure.problem_category == "environment"
        and failure.processing_status in (*ACTIVE, "resolved")
        and failure.replaced_by_id is None
        and node.node_type == "test_job"
        and node.case_type == "Daily"
        and node.result == "failure"
        and node.is_current
        and bool(node.source_id and node.source_run_id and run.repository)
        and run.deleted_at is None
    )


async def rerun_daily_case(db: AsyncSession, failure_id: int):
    run_id = await db.scalar(
        select(Node.run_id).join(Failure, Failure.node_id == Node.id).where(Failure.id == failure_id),
    )
    if run_id is None:
        raise LookupError("失败记录不存在")
    await db.rollback()

    async with DailyCollectionRepository(db).collection_lease(run_id) as acquired:
        if not acquired:
            raise ValueError("流水线正在采集，请稍后重试")
        row = (await db.execute(
            select(Failure, Node, Run)
            .join(Node, Node.id == Failure.node_id)
            .join(Run, Run.run_id == Node.run_id)
            .where(Failure.id == failure_id),
        )).one_or_none()
        if row is None:
            raise LookupError("失败记录不存在")
        failure, node, run = row
        if not can_rerun_case(failure, node, run):
            raise ValueError("只能重跑已分类为环境问题的当前 Daily 失败用例")
        repository, source_run_id, job_id = run.repository, node.source_run_id, node.source_id
        pending = dict(run.pending_reruns or {})
        if str(source_run_id) in pending:
            raise ValueError("该流水线已有重跑任务，请等待采集完成")
        credential = daily_github_credential(await read_settings(db, "github_token"))
        if not credential:
            raise ValueError("请先配置 Daily GitHub Token")
        await db.rollback()

        async with DailyCollectionClient(repository, credential) as client:
            remote_run = await client.run(source_run_id)
            if remote_run.get("status") != "completed":
                raise ValueError("该流水线仍在执行，请等待完成后重跑")
            attempt = remote_run.get("run_attempt")
            if not isinstance(attempt, int) or attempt < 1:
                raise CollectionSourceError("GitHub 未返回有效的执行次数")
            jobs = await client.latest_jobs(source_run_id, attempt)
            if not any(job["id"] == job_id and execution_result(job) == "failure" for job in jobs):
                raise ValueError("GitHub 上的用例执行结果已更新，请先刷新采集记录")
            await client.rerun_job(job_id)

        pending[str(source_run_id)] = attempt + 1
        await db.execute(
            update(Run).where(Run.run_id == run_id, Run.deleted_at.is_(None))
            .values(pending_reruns=pending, finished_at=None),
        )
        await db.commit()
        logger.info("Daily job rerun accepted run_id=%s source_run_id=%s job_id=%s", run_id, source_run_id, job_id)

    try:
        await start_collection(run_id)
    except SchedulerUpdateError:
        logger.warning("Daily job rerun collection registration failed run_id=%s", run_id)
        return {"collectionQueued": False}
    return {"collectionQueued": True}
