"""
通用后台任务追踪器

提供基于数据库的后台任务生命周期管理，支持：
- 状态机驱动：pending -> running -> completed / failed / partial
- 进度更新：百分比 + 阶段消息
- asyncio 异步任务托管（非阻塞 HTTP 响应）
- 任务去重（同一任务类型 + 参数同时只能有一个 running）
- 任务列表 / 详情查询

任何模块都可以使用此工具类追踪后台任务的执行状态。

使用示例：
    # 1. 创建并启动任务（HTTP 端点中，立即返回 task_id）
    task_id = await BackgroundTaskTracker.create_and_run(
        task_type="contributor_sync",
        task_func=my_async_collect_func,
        task_params={"repo_name": "owner/repo"},
        db=db,
    )
    return {"task_id": task_id}

    # 2. 查询任务状态
    task = await BackgroundTaskTracker.get_task(db, task_id)

    # 3. 在任务函数中更新进度
    async def my_async_collect_func(params: dict, tracker: BackgroundTaskTracker):
        await tracker.update_progress(10, "正在拉取数据...")
        await tracker.update_status("running", "开始处理")
        # ... 执行业务逻辑
        await tracker.complete(result_metadata={"records": 100})
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable

from sqlalchemy import Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import JSON, Float, TIMESTAMP

logger = logging.getLogger(__name__)

# 全局 asyncio task 引用字典：task_id → asyncio.Task
_active_tasks: dict[int, asyncio.Task] = {}


class TaskCancelled(Exception):
    """后台任务被取消时抛出的异常"""
    pass


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"           # 已创建，等待执行
    RUNNING = "running"           # 正在执行
    COMPLETED = "completed"       # 成功完成
    FAILED = "failed"             # 完全失败
    PARTIAL = "partial"          # 部分成功（部分仓库成功，部分失败）
    CANCELLED = "cancelled"     # 用户取消


# 合法状态转移
_VALID_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIAL, TaskStatus.CANCELLED},
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """检查状态转移是否合法"""
    allow = _VALID_TRANSITIONS.get(from_status, set())
    return to_status in allow


class BackgroundTaskTracker:
    """通用后台任务追踪器"""

    TABLE_NAME = "background_task_records"

    # ── 表初始化 ──────────────────────────────────────────────

    @staticmethod
    async def ensure_table(db: AsyncSession) -> None:
        """确保任务记录表存在（幂等操作）

        在受限权限环境中（如生产环境只读用户），跳过 CREATE TABLE，
        假设表已由迁移脚本创建。如果表实际不存在，后续操作会抛出
        明确的 'Table doesn't exist' 错误。
        """
        try:
            # 先检查表是否存在
            result = await db.execute(text(f"""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE()
                AND table_name = '{BackgroundTaskTracker.TABLE_NAME}'
            """))
            table_exists = result.scalar() > 0

            if table_exists:
                logger.debug("Table %s already exists", BackgroundTaskTracker.TABLE_NAME)
                return

            logger.info("Creating table %s", BackgroundTaskTracker.TABLE_NAME)
            await db.execute(text(f"""
                CREATE TABLE {BackgroundTaskTracker.TABLE_NAME} (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    task_type VARCHAR(100) NOT NULL,
                    task_params JSON,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    repo_name VARCHAR(200),
                    progress_percent FLOAT DEFAULT 0,
                    progress_message VARCHAR(500),
                    result_metadata JSON,
                    error_message TEXT,
                    started_at TIMESTAMP NULL,
                    completed_at TIMESTAMP NULL,
                    duration_seconds FLOAT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_task_type_status (task_type, status),
                    INDEX idx_status_created (status, created_at),
                    INDEX idx_created_at (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            await db.commit()
            logger.info("Table %s created successfully", BackgroundTaskTracker.TABLE_NAME)
        except Exception as e:
            error_msg = str(e)
            # CREATE 权限不足时，表可能已由迁移脚本创建，继续即可
            if "1142" in error_msg or "CREATE command denied" in error_msg:
                logger.warning(
                    "Current user lacks CREATE privilege for %s. "
                    "Assuming table was created by migration. "
                    "If the table does not exist, run: bash operations/production/migrate.sh",
                    BackgroundTaskTracker.TABLE_NAME,
                )
                return
            logger.error(
                "Failed to ensure table %s exists: %s",
                BackgroundTaskTracker.TABLE_NAME, e, exc_info=True
            )

    # ── 任务创建与启动 ────────────────────────────────────────

    @staticmethod
    async def create(
        db: AsyncSession,
        task_type: str,
        task_params: dict[str, Any] | None = None,
        repo_name: str | None = None,
    ) -> int:
        """
        创建一条任务记录，状态为 pending。

        Returns:
            新创建的任务记录 ID
        
        Raises:
            RuntimeError: 当表不存在时
        """
        import json as _json

        try:
            result = await db.execute(
                text(f"""
                    INSERT INTO {BackgroundTaskTracker.TABLE_NAME}
                        (task_type, task_params, status, repo_name, progress_message)
                    VALUES (:task_type, :task_params, 'pending', :repo_name, '任务已创建，等待执行')
                """),
                {
                    "task_type": task_type,
                    "task_params": _json.dumps(task_params or {}),
                    "repo_name": repo_name,
                },
            )
            task_id = result.lastrowid  # type: ignore[attr-defined]
            await db.commit()
            logger.info("Created background task %d type=%s", task_id, task_type)
            return task_id
        except Exception as e:
            logger.error(
                "Failed to create background task (type=%s, repo=%s): %s",
                task_type, repo_name, e, exc_info=True
            )
            # 检查是否是表不存在导致的错误
            error_msg = str(e).lower()
            if "table" in error_msg and "doesn't exist" in error_msg:
                raise RuntimeError(
                    f"Background task table '{BackgroundTaskTracker.TABLE_NAME}' does not exist. "
                    "Please run database migration or call ensure_table() first."
                ) from e
            raise

    @staticmethod
    async def create_and_run(
        task_type: str,
        task_func: Callable,
        task_params: dict[str, Any] | None = None,
        repo_name: str | None = None,
        db: AsyncSession | None = None,
    ) -> int:
        """
        创建任务记录并在后台异步执行，HTTP 端点立即返回。

        Args:
            task_type: 任务类型标识（如 "contributor_sync"）
            task_func: 异步函数，签名应为 async def func(params: dict, tracker: BackgroundTaskTracker)
            task_params: 传递给 task_func 的参数
            repo_name: 关联的仓库名（可选）
            db: 用于创建记录的 session（可选，不传则内部创建独立 session）

        Returns:
            任务记录 ID，前端可据此轮询状态
        """
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        if db:
            task_id = await BackgroundTaskTracker.create(db, task_type, task_params, repo_name)
        else:
            async with AsyncSessionLocal() as create_db:
                task_id = await BackgroundTaskTracker.create(create_db, task_type, task_params, repo_name)

        # 在后台启动任务，不阻塞 HTTP 响应
        task = asyncio.create_task(
            BackgroundTaskTracker._run_task(task_id, task_func, task_params or {}),
            name=f"bg-task-{task_type}-{task_id}",
        )
        _active_tasks[task_id] = task
        # task 执行完毕后自动清理引用
        task.add_done_callback(lambda t: _active_tasks.pop(task_id, None))

        logger.info("Scheduled background task %d type=%s", task_id, task_type)
        return task_id

    @staticmethod
    async def cancel(task_id: int) -> bool:
        """
        取消/终止一个正在运行的后台任务。
        方法：取消 asyncio.Task + 数据库状态设为 failed + 错误信息记录取消原因。

        Returns:
            True 如果成功取消，False 如果任务不存在或已不在运行中
        """
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        # 检查任务是否存在且处于 running 状态
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"SELECT status FROM {BackgroundTaskTracker.TABLE_NAME} WHERE id = :id"),
                {"id": task_id},
            )
            row = result.fetchone()
            if not row:
                logger.warning("cancel: task %d not found", task_id)
                return False
            current_status = row[0]
            if current_status not in ("running", "pending", "cancelled"):
                logger.warning("cancel: task %d status=%s cannot cancel", task_id, current_status)
                return False

        # 取消 asyncio task
        task = _active_tasks.get(task_id)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled asyncio task %d", task_id)

        # 更新数据库状态
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(f"""
                    UPDATE {BackgroundTaskTracker.TABLE_NAME}
                    SET status = 'cancelled', error_message = '任务被用户取消',
                        completed_at = NOW()
                    WHERE id = :id
                """),
                {"id": task_id},
            )
            await db.commit()

        return True

    @staticmethod
    async def _run_task(
        task_id: int,
        task_func: Callable,
        task_params: dict[str, Any],
    ) -> None:
        """内部方法：在 asyncio task 中执行后台任务"""
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        tracker = BackgroundTaskTracker(task_id)
        try:
            await tracker.update_status(TaskStatus.RUNNING, "任务开始执行")
        except Exception as e:
            logger.error("Failed to start task %d: %s", task_id, e)
            return

        try:
            result = await task_func(task_params, tracker)

            # 如果任务函数没有自行调用 complete/fail，自动标记完成
            current = await tracker.get_current_status()
            if current in (TaskStatus.RUNNING, TaskStatus.PENDING):
                await tracker.complete(result_metadata=result)

        except Exception as e:
            logger.error("Background task %d failed: %s", task_id, e, exc_info=True)
            try:
                await tracker.fail(str(e))
            except Exception:
                pass

    # ── 实例方法（操作单条任务记录）────────────────────────────

    def __init__(self, task_id: int):
        self.task_id = task_id

    async def is_cancelled(self) -> bool:
        """检查任务是否已被取消"""
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"SELECT status FROM {self.TABLE_NAME} WHERE id = :id"),
                {"id": self.task_id},
            )
            row = result.scalar_one_or_none()
            return row == "failed" and _active_tasks.get(self.task_id) is not None

    async def update_status(
        self,
        status: str,
        message: str | None = None,
        progress_percent: float | None = None,
    ) -> None:
        """更新任务状态"""
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        current = await self.get_current_status()

        if not is_valid_transition(current, status):
            logger.warning(
                "Invalid status transition for task %d: %s -> %s (skipping)",
                self.task_id, current, status,
            )
            return

        async with AsyncSessionLocal() as db:
            updates: list[str] = ["status = :status"]
            params: dict[str, Any] = {"status": status, "task_id": self.task_id}

            if message:
                # Truncate to fit VARCHAR(500) column
                truncated = (message[:497] + "...") if len(message) > 500 else message
                updates.append("progress_message = :message")
                params["message"] = truncated
            if progress_percent is not None:
                updates.append("progress_percent = :pct")
                params["pct"] = progress_percent
            if status == TaskStatus.RUNNING:
                updates.append("started_at = NOW(3)")
            elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIAL):
                updates.append("completed_at = NOW(3)")

                if status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL):
                    # Calculate duration
                    updates.append("duration_seconds = TIMESTAMPDIFF(SECOND, COALESCE(started_at, created_at), NOW())")

            await db.execute(
                text(f"UPDATE {self.TABLE_NAME} SET {', '.join(updates)} WHERE id = :task_id"),
                params,
            )
            await db.commit()

        logger.debug("Task %d status -> %s: %s", self.task_id, status, message or "")

    async def update_progress(
        self,
        progress_percent: float,
        message: str | None = None,
        result_metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        更新任务进度（不改变状态）

        Args:
            progress_percent: 0-100 的进度百分比
            message: 当前阶段描述
            result_metadata: 中间结果元数据
        """
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal
        import json as _json

        updates: list[str] = ["progress_percent = :pct"]
        params: dict[str, Any] = {"pct": min(100, max(0, progress_percent)), "task_id": self.task_id}
        if message:
            updates.append("progress_message = :message")
            params["message"] = message
        if result_metadata:
            updates.append("result_metadata = :meta")
            params["meta"] = _json.dumps(result_metadata)

        async with AsyncSessionLocal() as db:
            await db.execute(
                text(f"UPDATE {self.TABLE_NAME} SET {', '.join(updates)} WHERE id = :task_id"),
                params,
            )
            await db.commit()

    async def complete(self, result_metadata: dict[str, Any] | None = None) -> None:
        """标记任务完成"""
        await self.update_status(
            TaskStatus.COMPLETED,
            message="任务执行完成",
            progress_percent=100,
        )
        if result_metadata:
            await self.update_progress(100, "任务执行完成", result_metadata)

    async def fail(self, error_message: str) -> None:
        """标记任务失败"""
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        await self.update_status(TaskStatus.FAILED, message=f"任务执行失败: {error_message}")

        async with AsyncSessionLocal() as db:
            await db.execute(
                text(f"UPDATE {self.TABLE_NAME} SET error_message = :err WHERE id = :id"),
                {"err": error_message, "id": self.task_id},
            )
            await db.commit()

    async def partial(
        self,
        error_message: str | None = None,
        result_metadata: dict[str, Any] | None = None,
    ) -> None:
        """标记任务部分成功"""
        await self.update_status(
            TaskStatus.PARTIAL,
            message=f"任务部分完成: {error_message or '部分子任务失败'}",
        )
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            if error_message:
                await db.execute(
                    text(f"UPDATE {self.TABLE_NAME} SET error_message = :err WHERE id = :id"),
                    {"err": error_message, "id": self.task_id},
                )
                await db.commit()
            if result_metadata:
                import json as _json
                await db.execute(
                    text(f"UPDATE {self.TABLE_NAME} SET result_metadata = :meta WHERE id = :idx"),
                    {"meta": _json.dumps(result_metadata), "idx": self.task_id},
                )
                await db.commit()

    # ── 查询方法 ──────────────────────────────────────────────

    @staticmethod
    async def get_task(db: AsyncSession, task_id: int) -> dict[str, Any] | None:
        """获取单条任务记录的详细信息"""
        result = await db.execute(
            text(f"SELECT * FROM {BackgroundTaskTracker.TABLE_NAME} WHERE id = :id"),
            {"id": task_id},
        )
        row = result.mapping.first()
        return dict(row) if row else None

    async def get_current_status(self) -> str:
        """获取当前任务状态"""
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"SELECT status FROM {self.TABLE_NAME} WHERE id = :id"),
                {"id": self.task_id},
            )
            row = result.scalar_one_or_none()
            return row or TaskStatus.PENDING

    @staticmethod
    async def list_tasks(
        db: AsyncSession,
        task_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        查询任务列表

        Returns:
            (任务列表, 总数)
        """
        conditions = []
        params: dict[str, Any] = {}

        if task_type:
            conditions.append("task_type = :task_type")
            params["task_type"] = task_type
        if status:
            conditions.append("status = :status")
            params["status"] = status

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_result = await db.execute(
            text(f"SELECT COUNT(*) as cnt FROM {BackgroundTaskTracker.TABLE_NAME} {where_sql}"),
            params,
        )
        total = int(count_result.scalar_one_or_none() or 0)

        query_params = {**params, "limit": limit, "offset": offset}
        rows_result = await db.execute(
            text(f"""
                SELECT * FROM {BackgroundTaskTracker.TABLE_NAME} {where_sql}
                ORDER BY created_at DESC LIMIT :limit OFFSET :offset
            """),
            query_params,
        )
        records = [dict(r.mapping()) for r in rows_result]
        return records, total

    @staticmethod
    async def get_active_task(
        db: AsyncSession,
        task_type: str,
        repo_name: str | None = None,
    ) -> dict[str, Any] | None:
        """
        检查是否存在正在运行的同类任务（去重用）

        Returns:
            正在运行的任务记录，或 None
        """
        query = text(f"""
            SELECT * FROM {BackgroundTaskTracker.TABLE_NAME}
            WHERE task_type = :task_type AND status IN ('pending', 'running')
        """)
        params: dict[str, Any] = {"task_type": task_type}
        if repo_name:
            query = text(f"""
                SELECT * FROM {BackgroundTaskTracker.TABLE_NAME}
                WHERE task_type = :task_type AND repo_name = :repo_name AND status IN ('pending', 'running')
            """)
            params["repo_name"] = repo_name

        result = await db.execute(query, params)
        row = result.mappings().first()
        return dict(row) if row else None
