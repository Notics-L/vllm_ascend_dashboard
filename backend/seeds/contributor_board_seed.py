"""
贡献者看板模拟数据种子脚本

用法:
    cd backend
    uv run python seeds/contributor_board_seed.py          # 仅生成模拟数据
    uv run python seeds/contributor_board_seed.py --clear   # 先清空现有数据再生成

说明:
    生成 20 个贡献者，分布在四个象限，每个贡献者包含:
    - 6 个月(按月) 的 ContributorMetrics 趋势数据
    - CommitClassification 提交分类记录
    - ImageBuildPlan 构建计划记录
    - ErrorRecord revert 记录
    - 自动计算 role_types
"""

import argparse
import asyncio
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, delete

# Add parent dir to path
sys.path.insert(0, "..")

from contributor_board.role_analytics import detect_roles
from infrastructure.db.base import SessionLocal as AsyncSessionLocal
from infrastructure.persistence.models import (
    CommitClassification,
    Contributor,
    ContributorMetrics,
    ErrorRecord,
    ImageBuildPlan,
    QuadrantSnapshot,
)

random.seed(42)


# ── 贡献者定义 ──────────────────────────────────────────────
# 四个象限: INCENTIVE(高贡献低出错) / IMPROVE(高贡献高出错) / ENCOURAGE(低贡献低出错) / REPLACE(低贡献高出错)

CONTRIBUTORS = [
    # INCENTIVE - Star contributors (5)
    {"login": "ykato", "name": "Yushi Kato", "github_id": 1001, "is_community": True, "company": "Sony", "quadrant": "INCENTIVE"},
    {"login": "adam", "name": "Adam", "github_id": 1002, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "xwjiang", "name": "Xiangwang Jiang", "github_id": 1003, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "hjiang", "name": "Hexiang Jiang", "github_id": 1004, "is_community": True, "company": "Stanford", "quadrant": "INCENTIVE"},
    {"login": "david", "name": "David Wu", "github_id": 1005, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    # INCENTIVE - Active contributors (5)
    {"login": "jiasenli", "name": "Jiachen Lin", "github_id": 1006, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "michael", "name": "Michael Zhang", "github_id": 1007, "is_community": True, "company": "NVIDIA", "quadrant": "INCENTIVE"},
    {"login": "alicechen", "name": "Alice Chen", "github_id": 1008, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "bobwang", "name": "Bob Wang", "github_id": 1009, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "carolliu", "name": "Carol Liu", "github_id": 1010, "is_community": True, "company": "Tsinghua", "quadrant": "INCENTIVE"},
    # IMPROVE - High contrib, high error (4)
    {"login": "erranddev1", "name": "Errand Developer 1", "github_id": 1011, "is_community": True, "company": "Community", "quadrant": "IMPROVE"},
    {"login": "erranddev2", "name": "Errand Developer 2", "github_id": 1012, "is_community": True, "company": "Alibaba", "quadrant": "IMPROVE"},
    {"login": "erranddev3", "name": "Errand Developer 3", "github_id": 1013, "is_community": True, "company": "Baidu", "quadrant": "IMPROVE"},
    {"login": "erranddev4", "name": "Errand Developer 4", "github_id": 1014, "is_community": True, "company": "Tencent", "quadrant": "IMPROVE"},
    # ENCOURAGE - Low contrib, low error (4)
    {"login": "newbie1", "name": "Newbie Dev 1", "github_id": 1015, "is_community": True, "company": "Student", "quadrant": "ENCOURAGE"},
    {"login": "newbie2", "name": "Newbie Dev 2", "github_id": 1016, "is_community": True, "company": "Community", "quadrant": "ENCOURAGE"},
    {"login": "newbie3", "name": "Newbie Dev 3", "github_id": 1017, "is_community": True, "company": "Student", "quadrant": "ENCOURAGE"},
    {"login": "newbie4", "name": "Newbie Dev 4", "github_id": 1018, "is_community": True, "company": "Community", "quadrant": "ENCOURAGE"},
    # REPLACE - Low contrib, high error (2)
    {"login": "toxicdev1", "name": "Toxic Dev 1", "github_id": 1019, "is_community": True, "company": "External", "quadrant": "REPLACE"},
    {"login": "toxicdev2", "name": "Toxic Dev 2", "github_id": 1020, "is_community": True, "company": "External", "quadrant": "REPLACE"},
]

MODULES = ["csrc", "attention", "MoE", "EPLB", "MRV2", "decoder", "scheduler", "kv_cache", "tokenizer", "config"]
COMMIT_TYPES = ["BugFix", "Feature", "Performance", "Doc", "Test", "Refactor", "CI", "Misc"]
COMMIT_TYPE_WEIGHTS = [30, 25, 10, 15, 8, 5, 3, 4]

COMMIT_MESSAGES = {
    "BugFix": [
        'fix(attention): correct kernel launch config for A5',
        'fix(kv_cache): handle edge case in cache eviction',
        'fix: resolve OOM on 910B with large batch',
        'fix(MoE): balance expert load across NPU',
        'fix: correct dtype handling for w8a8 models',
    ],
    "Feature": [
        'feat(EPLB): add speculative decoding support',
        'feat: support Qwen3-235B on A5',
        'feat(scheduler): implement dynamic batch sizing',
        'feat: add FP8 quantization pipeline',
        'feat(MRV2): implement new attention backend',
    ],
    "Performance": [
        'perf(attention): optimize 310P memory copy',
        'perf: reduce kernel launch overhead by 15%',
        'perf(kv_cache): use shared memory for small blocks',
        'perf: batch tokenization for high-throughput',
    ],
    "Doc": [
        'docs: add A5 hardware setup guide',
        'docs: update performance benchmark results',
        'docs: fix typo in README',
    ],
    "Test": [
        'test: add e2e test for MoE routing',
        'test(ci): add 310P nightly coverage',
        'test: improve flaky test reliability',
    ],
    "Refactor": [
        'refactor(scheduler): simplify batch allocation logic',
        'refactor: extract common kernel utilities',
    ],
    "CI": [
        'ci: update runner image to v2024.10',
        'ci: add 910C testing stage',
    ],
    "Misc": [
        'chore: update dependency versions',
        'misc: cleanup unused imports',
    ],
}

BUILD_TYPES = ["full_build", "minimal_build", "nightly_only", "skip", "smart_check", "conservative"]
BUILD_TYPE_WEIGHTS = [25, 20, 15, 15, 15, 10]


def generate_commit_sha() -> str:
    return uuid.uuid4().hex[:40]


def get_monthly_periods(months: int = 6) -> list[tuple[date, date]]:
    """Generate monthly periods from (now - months) to now."""
    today = date.today()
    periods = []
    for i in range(months, 0, -1):
        end = today.replace(day=1) - timedelta(days=1) - timedelta(days=(i - 1) * 30) + timedelta(days=30)
        end = min(end, today)
        start = end - timedelta(days=29)
        periods.append((start, end))
    if not periods:
        end = today
        start = end - timedelta(days=59)
        periods.append((start, end))
    return periods


def generate_monthly_metrics(quadrant: str, months: int = 6) -> list[dict]:
    """Generate monthly metric aggregates for a contributor based on their quadrant."""
    profiles = {
        "INCENTIVE": {"pr_range": (5, 15), "code_range": (500, 3000), "review_range": (10, 40), "approval_range": (5, 20), "merge_range": (3, 15), "error_range": (0, 1), "core_range": (2, 8)},
        "IMPROVE": {"pr_range": (5, 12), "code_range": (400, 2000), "review_range": (5, 20), "approval_range": (2, 10), "merge_range": (1, 5), "error_range": (3, 8), "core_range": (1, 5)},
        "ENCOURAGE": {"pr_range": (0, 3), "code_range": (0, 200), "review_range": (0, 5), "approval_range": (0, 2), "merge_range": (0, 1), "error_range": (0, 1), "core_range": (0, 2)},
        "REPLACE": {"pr_range": (0, 3), "code_range": (0, 200), "review_range": (0, 3), "approval_range": (0, 1), "merge_range": (0, 1), "error_range": (4, 10), "core_range": (0, 1)},
    }
    p = profiles[quadrant]
    periods = get_monthly_periods(months)
    metrics = []
    for start, end in periods:
        m = {
            "period_start": start,
            "period_end": end,
            "merged_pr_count": random.randint(*p["pr_range"]),
            "code_additions": random.randint(*p["code_range"]),
            "code_deletions": random.randint(0, random.randint(*p["code_range"]) // 2),
            "review_count": random.randint(*p["review_range"]),
            "approval_count": random.randint(*p["approval_range"]),
            "merge_count": random.randint(*p["merge_range"]),
            "core_module_count": random.randint(*p["core_range"]),
            "closed_pr_count": random.randint(0, 2),
            "error_score": round(random.uniform(*p["error_range"]), 2),
            "quadrant": quadrant,
        }
        m["contribution_score"] = round(
            m["merged_pr_count"] * 10.0 + m["code_additions"] * 0.5 + m["review_count"] * 3.0 + m["approval_count"] * 5.0 + m["core_module_count"] * 8.0,
            2,
        )
        metrics.append(m)
    return metrics


async def seed(db, clear: bool = False):
    """Run the seed."""
    if clear:
        for model in [QuadrantSnapshot, ErrorRecord, ImageBuildPlan, CommitClassification, ContributorMetrics, Contributor]:
            await db.execute(delete(model))
        await db.commit()
        print("[seed] Cleared existing contributor_board data")

    # 1) Insert contributors
    contributor_map = {}
    now = datetime.now(UTC)
    six_months_ago = date.today() - timedelta(days=180)

    for c in CONTRIBUTORS:
        existing = await db.execute(select(Contributor).where(Contributor.github_id == c["github_id"]))
        existing_row = existing.scalar_one_or_none()
        if existing_row:
            contributor_map[c["login"]] = existing_row
            continue

        contrib = Contributor(
            github_id=c["github_id"],
            login=c["login"],
            avatar_url=f"https://avatars.githubusercontent.com/u/{c['github_id']}",
            email=f"{c['login']}@example.com",
            name=c["name"],
            is_community=c["is_community"],
            role_types=[],
            company=c["company"],
            first_contribution_at=datetime.combine(six_months_ago, datetime.min.time(), tzinfo=UTC),
            last_contribution_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(contrib)
        contributor_map[c["login"]] = contrib

    await db.flush()
    print(f"[seed] Created/loaded {len(contributor_map)} contributors")

    # 2) Insert ContributorMetrics (monthly, 6 months)
    metrics_count = 0
    commit_sha_counter = 0
    all_commits = []

    for c_info in CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]
        monthly = generate_monthly_metrics(c_info["quadrant"])

        for m in monthly:
            # Check unique constraint
            existing_stmt = select(ContributorMetrics).where(
                ContributorMetrics.contributor_login == login,
                ContributorMetrics.period_start == m["period_start"],
                ContributorMetrics.period_end == m["period_end"],
            )
            existing = (await db.execute(existing_stmt)).scalar_one_or_none()
            if existing:
                continue

            cm = ContributorMetrics(
                contributor_id=contrib.id,
                contributor_login=login,
                period_start=m["period_start"],
                period_end=m["period_end"],
                merged_pr_count=m["merged_pr_count"],
                code_additions=m["code_additions"],
                code_deletions=m["code_deletions"],
                review_count=m["review_count"],
                approval_count=m["approval_count"],
                merge_count=m["merge_count"],
                core_module_count=m["core_module_count"],
                closed_pr_count=m["closed_pr_count"],
                contribution_score=m["contribution_score"],
                error_score=m["error_score"],
                quadrant=m["quadrant"],
            )
            db.add(cm)
            metrics_count += 1

            # 3) Generate CommitClassification entries for this period
            num_commits = max(1, m["merged_pr_count"])
            for _ in range(num_commits):
                commit_sha_counter += 1
                ctype = random.choices(COMMIT_TYPES, weights=COMMIT_TYPE_WEIGHTS, k=1)[0]
                cmsg = random.choice(COMMIT_MESSAGES.get(ctype, ["misc commit"]))
                modules = random.sample(MODULES, k=random.randint(1, 3))
                is_chip = random.random() < 0.15
                chip = random.choice(["910B", "910C", "310P", "A5"]) if is_chip else None

                cc = CommitClassification(
                    commit_sha=generate_commit_sha(),
                    commit_message=cmsg,
                    commit_type=ctype,
                    confidence=round(random.uniform(0.8, 1.0), 2),
                    modules_touched=modules,
                    files_changed_count=random.randint(1, 20),
                    is_chip_specific=is_chip,
                    chip_target=chip,
                    contributor_login=login,
                    pr_number=random.randint(1, 500),
                    created_at=datetime.combine(m["period_start"] + timedelta(days=random.randint(0, 28)), datetime.min.time(), tzinfo=UTC),
                )
                db.add(cc)
                all_commits.append((cc, m))

    await db.flush()
    print(f"[seed] Created {metrics_count} ContributorMetrics entries")

    # 4) Revert/ErrorRecords for IMPROVE and REPLACE contributors
    error_count = 0
    for c_info in CONTRIBUTORS:
        if c_info["quadrant"] not in ("IMPROVE", "REPLACE"):
            continue
        login = c_info["login"]
        num_errors = random.randint(2, 6) if c_info["quadrant"] == "IMPROVE" else random.randint(3, 8)
        for _ in range(num_errors):
            er = ErrorRecord(
                contributor_login=login,
                error_type="revert",
                related_pr_number=random.randint(1, 500),
                related_commit_sha=generate_commit_sha(),
                revert_count=1,
                bugfix_linked_count=0,
                ci_failure_count=random.randint(0, 2),
                nightly_failure_count=random.randint(0, 1),
                weight_score=5.0,
                description=f'Revert "{random.choice(COMMIT_MESSAGES["Feature"])}"',
            )
            db.add(er)
            error_count += 1

    print(f"[seed] Created {error_count} ErrorRecord entries")

    # 5) ImageBuildPlan entries
    build_plan_count = 0
    for c_info in CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]
        num_plans = random.randint(3, 8)
        for _ in range(num_plans):
            btype = random.choices(BUILD_TYPES, weights=BUILD_TYPE_WEIGHTS, k=1)[0]
            bp = ImageBuildPlan(
                pr_number=random.randint(1, 500),
                commit_sha=generate_commit_sha(),
                commit_type=random.choice(COMMIT_TYPES),
                build_type=btype,
                chips=random.sample(["910B", "910C", "310P", "A5"], k=random.randint(1, 2)),
                os_list=["ubuntu22.04"],
                cache_strategy=random.choice(["layer_cache", "full_rebuild", "smart_cache"]),
                estimated_saving_percent=round(random.uniform(0, 80), 1),
                actual_execution=random.random() < 0.7,
                created_at=datetime.now(UTC) - timedelta(days=random.randint(1, 180)),
            )
            db.add(bp)
            build_plan_count += 1

    print(f"[seed] Created {build_plan_count} ImageBuildPlan entries")

    # 6) Update role_types based on aggregated metrics
    for c_info in CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]

        stmt = select(
            func.sum(ContributorMetrics.merged_pr_count),
            func.sum(ContributorMetrics.review_count),
            func.sum(ContributorMetrics.approval_count),
            func.sum(ContributorMetrics.merge_count),
            func.sum(ContributorMetrics.code_additions),
        ).where(ContributorMetrics.contributor_login == login)
        result = await db.execute(stmt)
        row = result.one()

        merged = int(row[0] or 0)
        reviews = int(row[1] or 0)
        approvals = int(row[2] or 0)
        merges = int(row[3] or 0)
        additions = int(row[4] or 0)

        roles = detect_roles(merged, reviews, approvals, merges, additions)
        contrib.role_types = roles

    await db.commit()
    print(f"[seed] Updated role_types for all contributors")
    print(f"[seed] Done! Total: {len(CONTRIBUTORS)} contributors, {metrics_count} metrics, {error_count} errors, {build_plan_count} build plans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed contributor board mock data")
    parser.add_argument("--clear", action="store_true", help="Clear existing data before seeding")
    args = parser.parse_args()

    async def main():
        async with AsyncSessionLocal() as db:
            await seed(db, clear=args.clear)

    asyncio.run(main())
