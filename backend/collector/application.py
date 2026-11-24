"""
Collector 独立进程入口。
通过 `python -m collector` 启动。

读取 NODE_ID 和 CAPABILITIES 环境变量，通过 FOR UPDATE SKIP LOCKED
竞争领取 collection_tasks 中的任务并执行。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from infrastructure.core.github_audit import github_audit_writer

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("collector")


@github_audit_writer("collector")
async def main():
    node_id = os.environ.get("NODE_ID", f"collector-{os.uname().nodename}")
    capabilities_str = os.environ.get("CAPABILITIES", "python")
    capabilities = [c.strip() for c in capabilities_str.split(",") if c.strip()]

    from infrastructure.db.base import SessionLocal

    from .executor import CollectorRunner
    from .worker import CollectorWorker

    worker = CollectorWorker(
        node_id=node_id,
        capabilities=capabilities,
        db_session_factory=SessionLocal,
        # Three analysis leases may run at once; keep a fourth worker slot for
        # higher-priority synchronization tasks. The database claim enforces
        # the analysis limit across all Collector processes.
        max_concurrent=4,
    )
    runner = CollectorRunner(worker)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
