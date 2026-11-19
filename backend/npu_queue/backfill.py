"""Controlled local/ops entrypoint for CI NPU fact backfill."""
from __future__ import annotations

import argparse
import asyncio

from collector.npu_job_facts import backfill_npu_job_facts
from infrastructure.db.base import SessionLocal, engine


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill normalized CI NPU job facts")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--limit", type=int, default=1_000)
    args = parser.parse_args()
    try:
        async with SessionLocal() as db:
            count = await backfill_npu_job_facts(db, repository=args.repository, limit=args.limit)
            await db.commit()
        print(f"Backfilled {count} CI NPU job facts for {args.repository}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
