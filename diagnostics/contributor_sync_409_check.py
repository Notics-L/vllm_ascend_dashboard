#!/usr/bin/env python3
"""
诊断并修复 /contributor-board/sync/contributors 返回 409「已有同步任务正在运行」

原理：
- 409 由 contributor_sync_records 中存在 status IN ('running','pending') 的记录触发
- 真在跑：started_at 很近 且 有关联的 running 后台任务
- 僵尸：started_at 缺失、或运行异常久(>3h)、或无关联后台任务（常见于服务重启/任务被杀）

用法（生产服务器 /root/vllm_ascend_dashboard）：
  1. 只诊断（默认，不改库）:
       uv run python diagnostics/contributor_sync_409_check.py
  2. 诊断 + 自动清理僵尸（先备份！）:
       uv run python diagnostics/contributor_sync_409_check.py --fix
     （--fix 只会把「运行>3h 或 started_at 为空」的 running/pending 置 failed）

AGENTS.md 红线：清库前先 bash operations/production/backup.sh
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

STALE_RUNNING_SEC = 3 * 3600  # 超过即视为僵尸候选（限流等待最长约 2h，已留余量）


def _stale_rid(rid: int, started, alive: bool) -> bool:
    """判断某 record 是否为僵尸：started 缺失 / 超 3h / 无关联后台任务"""
    if started is None:
        return True
    age = (datetime.now(UTC) - started).total_seconds()
    if age > STALE_RUNNING_SEC:
        return True
    if not alive:  # 无 running 后台任务且已跑较久
        return age > 600  # 10 分钟以上无后台任务即疑
    return False


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="自动清理僵尸 running/pending 记录")
    args = ap.parse_args()

    from sqlalchemy import text
    from infrastructure.db.base import SessionLocal

    now = datetime.now(UTC)
    print("=" * 80)
    print(f"409 / 僵尸 running 诊断  (now={now.isoformat()})  fix={args.fix}")
    print("=" * 80)

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                text("""
                    SELECT id, repo_name, sync_type, status,
                           progress_percent, progress_message,
                           started_at, completed_at, created_at, trigger_source
                    FROM contributor_sync_records
                    WHERE status IN ('running','pending')
                    ORDER BY created_at DESC
                """)
            )
        ).fetchall()

        if not rows:
            print("\n✅ 没有 running/pending 同步记录 → 不应再 409。")
            print("   若仍 409：请确认请求带管理员 token，且命中的是同一服务实例。")
            return 0

        # 关联后台任务
        btask_map: dict[int, tuple] = {}
        try:
            for bt in (
                await db.execute(
                    text(
                        """
                        SELECT id, status, created_at, COALESCE(task_params,'{}')
                        FROM background_task_records
                        WHERE task_type='contributor_sync' AND status IN ('pending','running')
                        ORDER BY created_at DESC
                        """
                    )
                )
            ).fetchall():
                try:
                    sid = json.loads(bt[3]).get("sync_record_id")
                    if sid is not None:
                        btask_map[int(sid)] = (bt[0], bt[1], bt[2])
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ 查询 background_task_records 失败: {e}")

        print(f"\n发现 {len(rows)} 条 running/pending 记录（409 来源）:\n")
        stale_rids: list[int] = []
        for r in rows:
            (rid, repo, stype, status, pct, msg,
             started, completed, created, source) = r
            alive = rid in btask_map
            age = 0.0 if started is None else (now - started).total_seconds()

            print(f"[record {rid}]  repo={repo}  type={stype}  status={status}")
            print(f"    progress={pct}%  msg={msg!r}")
            print(f"    started_at={started}  completed_at={completed}  source={source}")
            if started is not None:
                print(f"    已运行 {age:.0f}s ({age / 3600:.2f}h)")
            if alive:
                bt_id, bt_status, bt_c = btask_map[rid]
                print(f"    ↳ 关联后台任务 {bt_id}: status={bt_status}  (看起来仍在跑)")
            else:
                print("    ↳ 无 running 后台任务")

            is_stale = _stale_rid(rid, started, alive)
            if is_stale:
                stale_rids.append(rid)
                print("    ⚠️  疑似僵尸 → 可清理\n")
            else:
                print("    ✅ 可能在正常执行，建议等待\n")

        print("-" * 80)
        if not stale_rids:
            print("结论：所有 running 记录看起来都还在正常执行，无僵尸。等待其完成即可。")
            return 0

        print(f"结论：检测到 {len(stale_rids)} 条疑似僵尸: {stale_rids}")
        if not args.fix:
            print("未传 --fix，不做修改。确认是僵尸后运行:")
            print("  bash operations/production/backup.sh   # 先备份（AGENTS.md 红线）")
            print("  uv run python diagnostics/contributor_sync_409_check.py --fix")
            return 1

        # 执行清理（带 id 白名单，最安全）
        placeholders = ",".join(str(int(x)) for x in stale_rids)
        res = await db.execute(
            text(
                f"UPDATE contributor_sync_records "
                f"SET status='failed', completed_at=NOW(), "
                f"progress_message='stale running auto-cleared by ops' "
                f"WHERE id IN ({placeholders}) AND status IN ('running','pending')"
            )
        )
        await db.commit()
        print(f"\n✅ 已清理 {res.rowcount} 条僵尸记录。现在应可重新触发同步。")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
