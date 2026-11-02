"""
贡献者看板模拟数据种子脚本（重构版 — 基于 ContributorProfile 宽表）

用法:
    cd backend
    uv run python seeds/contributor_board_seed.py          # 仅生成模拟数据
    uv run python seeds/contributor_board_seed.py --clear   # 先清空现有数据再生成

说明:
    生成 20 个贡献者，分布在四个象限，每个贡献者写入 contributor_profiles 一行，
    包含 7 维原始指标 + review 维度 + total_score/tier/role。
    同时写入 contributors 身份表和 image_build_plans 构建优化记录。
    scoring_engine 的 calculate_score / assign_tier / infer_role 负责打分。
"""

import argparse
import asyncio
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select

# Add parent dir to path
sys.path.insert(0, "..")

from contributor_board.role_analytics import detect_roles
from contributor_board.scoring_engine import (
    assign_tier,
    calculate_score,
    get_default_weights,
    infer_role,
)
from infrastructure.db.base import SessionLocal as AsyncSessionLocal
from infrastructure.persistence.models import (
    Contributor,
    ContributorProfile,
    ImageBuildPlan,
)

random.seed(42)


# ── 贡献者定义 ──────────────────────────────────────────────
# 四个象限: INCENTIVE / IMPROVE / ENCOURAGE / REPLACE
# 7 维生成范围（按象限）
#   commit_count, code_files, doc_files, pr_merged, pr_open, pr_closed,
#   active_days_90, span_days, review_count, approval_count

CONTRIBUTORS = [
    # INCENTIVE - Star contributors (5)
    {"login": "ykato", "name": "Yushi Kato", "github_id": 1001, "is_community": True, "company": "Sony", "tier": "INCENTIVE"},
    {"login": "adam", "name": "Adam", "github_id": 1002, "is_community": False, "company": "Huawei", "tier": "INCENTIVE"},
    {"login": "xwjiang", "name": "Xiangwang Jiang", "github_id": 1003, "is_community": False, "company": "Huawei", "tier": "INCENTIVE"},
    {"login": "hjiang", "name": "Hexiang Jiang", "github_id": 1004, "is_community": True, "company": "Stanford", "tier": "INCENTIVE"},
    {"login": "david", "name": "David Wu", "github_id": 1005, "is_community": False, "company": "Huawei", "tier": "INCENTIVE"},
    # INCENTIVE - Active contributors (5)
    {"login": "jiasenli", "name": "Jiachen Lin", "github_id": 1006, "is_community": False, "company": "Huawei", "tier": "INCENTIVE"},
    {"login": "michael", "name": "Michael Zhang", "github_id": 1007, "is_community": True, "company": "NVIDIA", "tier": "INCENTIVE"},
    {"login": "alicechen", "name": "Alice Chen", "github_id": 1008, "is_community": False, "company": "Huawei", "tier": "INCENTIVE"},
    {"login": "bobwang", "name": "Bob Wang", "github_id": 1009, "is_community": False, "company": "Huawei", "tier": "INCENTIVE"},
    {"login": "carolliu", "name": "Carol Liu", "github_id": 1010, "is_community": True, "company": "Tsinghua", "tier": "INCENTIVE"},
    # IMPROVE - High contrib, low review_ratio (4)
    {"login": "erranddev1", "name": "Errand Developer 1", "github_id": 1011, "is_community": True, "company": "Community", "tier": "IMPROVE"},
    {"login": "erranddev2", "name": "Errand Developer 2", "github_id": 1012, "is_community": True, "company": "Alibaba", "tier": "IMPROVE"},
    {"login": "erranddev3", "name": "Errand Developer 3", "github_id": 1013, "is_community": True, "company": "Baidu", "tier": "IMPROVE"},
    {"login": "erranddev4", "name": "Errand Developer 4", "github_id": 1014, "is_community": True, "company": "Tencent", "tier": "IMPROVE"},
    # ENCOURAGE - Low contrib, high review_ratio (4)
    {"login": "newbie1", "name": "Newbie Dev 1", "github_id": 1015, "is_community": True, "company": "Student", "tier": "ENCOURAGE"},
    {"login": "newbie2", "name": "Newbie Dev 2", "github_id": 1016, "is_community": True, "company": "Community", "tier": "ENCOURAGE"},
    {"login": "newbie3", "name": "Newbie Dev 3", "github_id": 1017, "is_community": True, "company": "Student", "tier": "ENCOURAGE"},
    {"login": "newbie4", "name": "Newbie Dev 4", "github_id": 1018, "is_community": True, "company": "Community", "tier": "ENCOURAGE"},
    # REPLACE - Low contrib, high error (2)
    {"login": "toxicdev1", "name": "Toxic Dev 1", "github_id": 1019, "is_community": True, "company": "External", "tier": "REPLACE"},
    {"login": "toxicdev2", "name": "Toxic Dev 2", "github_id": 1020, "is_community": True, "company": "External", "tier": "REPLACE"},
]

BUILD_TYPES = ["full_build", "minimal_build", "nightly_only", "skip", "smart_check", "conservative"]
BUILD_TYPE_WEIGHTS = [25, 20, 15, 15, 15, 10]
COMMIT_TYPES = ["BugFix", "Feature", "Performance", "Doc", "Test", "Refactor", "CI", "Misc"]


def generate_commit_sha() -> str:
    return uuid.uuid4().hex[:40]


def _generate_metrics(tier: str) -> dict:
    """按象限生成 7 维 + review 维度的原始指标。"""
    profiles = {
        "INCENTIVE": {
            "commit_count": (60, 200), "code_files": (30, 80), "doc_files": (0, 10),
            "pr_merged": (10, 40), "pr_open": (1, 5), "pr_closed": (0, 5),
            "active_days_90": (60, 90), "span_days": (300, 720),
            "review_count": (8, 30), "approval_count": (5, 20),
        },
        "IMPROVE": {
            "commit_count": (40, 120), "code_files": (20, 60), "doc_files": (0, 5),
            "pr_merged": (8, 25), "pr_open": (5, 15), "pr_closed": (5, 15),  # 高 open/closed → 低 merge_rate
            "active_days_90": (40, 70), "span_days": (200, 500),
            "review_count": (2, 8), "approval_count": (0, 5),
        },
        "ENCOURAGE": {
            "commit_count": (0, 10), "code_files": (0, 8), "doc_files": (0, 5),
            "pr_merged": (0, 3), "pr_open": (0, 3), "pr_closed": (0, 3),
            "active_days_90": (10, 40), "span_days": (30, 200),
            "review_count": (5, 25), "approval_count": (2, 15),  # 高 review_ratio
        },
        "REPLACE": {
            "commit_count": (0, 5), "code_files": (0, 5), "doc_files": (0, 2),
            "pr_merged": (0, 1), "pr_open": (2, 8), "pr_closed": (5, 15),  # 大量 closed 未 merged
            "active_days_90": (0, 10), "span_days": (10, 100),
            "review_count": (0, 3), "approval_count": (0, 1),
        },
    }
    p = profiles[tier]
    return {k: random.randint(lo, hi) for k, (lo, hi) in p.items()}


async def seed(db, clear: bool = False):
    """Run the seed."""
    if clear:
        for model in [ImageBuildPlan, ContributorProfile, Contributor]:
            await db.execute(delete(model))
        await db.commit()
        print("[seed] Cleared existing contributor_board data")

    # 1) Insert contributors
    contributor_map: dict[str, Contributor] = {}
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

    # 2) Insert ContributorProfile (one row per contributor, aggregated over 6 months)
    profile_count = 0
    weights = get_default_weights()
    repo_name = "vllm-project/vllm-ascend"

    for c_info in CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]
        m = _generate_metrics(c_info["tier"])

        # 计算 7 维分 + 分区 + 角色
        total_score = calculate_score(m, weights)
        tier = assign_tier(total_score, m["active_days_90"])
        role = infer_role({**m, "code_files": m.get("code_files", 0)})

        # review_ratio = review / (review + pr_total)
        pr_total = m["pr_merged"] + m["pr_open"] + m["pr_closed"]
        review_ratio = (m["review_count"] / (m["review_count"] + pr_total)) if (m["review_count"] + pr_total) > 0 else 0.0

        existing_stmt = select(ContributorProfile).where(
            ContributorProfile.login == login,
            ContributorProfile.repo_name == repo_name,
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            existing.commit_count = m["commit_count"]
            existing.code_files = m["code_files"]
            existing.doc_files = m["doc_files"]
            existing.pr_merged = m["pr_merged"]
            existing.pr_open = m["pr_open"]
            existing.pr_closed = m["pr_closed"]
            existing.active_days_90 = m["active_days_90"]
            existing.span_days = m["span_days"]
            existing.review_count = m["review_count"]
            existing.approval_count = m["approval_count"]
            existing.review_ratio = round(review_ratio, 4)
            existing.total_score = total_score
            existing.tier = tier
            existing.role = role
            profile_count += 1
            continue

        p = ContributorProfile(
            repo_name=repo_name,
            login=login,
            contributor_id=contrib.id,
            commit_count=m["commit_count"],
            code_files=m["code_files"],
            doc_files=m["doc_files"],
            pr_merged=m["pr_merged"],
            pr_open=m["pr_open"],
            pr_closed=m["pr_closed"],
            active_days_90=m["active_days_90"],
            span_days=m["span_days"],
            review_count=m["review_count"],
            approval_count=m["approval_count"],
            review_ratio=round(review_ratio, 4),
            total_score=total_score,
            tier=tier,
            role=role,
            first_seen=six_months_ago,
            last_seen=date.today(),
        )
        db.add(p)
        profile_count += 1

    await db.flush()
    print(f"[seed] Created {profile_count} ContributorProfile entries")

    # 3) ImageBuildPlan entries
    build_plan_count = 0
    for c_info in CONTRIBUTORS:
        login = c_info["login"]
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
                repo_name=repo_name,
                created_at=datetime.now(UTC) - timedelta(days=random.randint(1, 180)),
            )
            db.add(bp)
            build_plan_count += 1

    print(f"[seed] Created {build_plan_count} ImageBuildPlan entries")

    # 4) Update role_types on contributors based on profile metrics
    for c_info in CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]
        profile_stmt = select(ContributorProfile).where(
            ContributorProfile.login == login,
            ContributorProfile.repo_name == repo_name,
        )
        profile = (await db.execute(profile_stmt)).scalar_one_or_none()
        if not profile:
            continue
        roles = detect_roles(
            merged_pr_count=profile.pr_merged or 0,
            review_count=profile.review_count or 0,
            approval_count=profile.approval_count or 0,
            merge_count=profile.pr_merged or 0,
            code_additions=profile.code_files or 0,
        )
        contrib.role_types = roles

    await db.commit()
    print(f"[seed] Updated role_types for all contributors")
    print(f"[seed] Done! Total: {len(CONTRIBUTORS)} contributors, {profile_count} profiles, {build_plan_count} build plans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed contributor board mock data")
    parser.add_argument("--clear", action="store_true", help="Clear existing data before seeding")
    args = parser.parse_args()

    async def main():
        async with AsyncSessionLocal() as db:
            await seed(db, clear=args.clear)

    asyncio.run(main())
