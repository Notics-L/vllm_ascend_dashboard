"""Populate a local development database with realistic demo data.

This script is deliberately development-only. It creates representative
records for the dashboard pages and writes the file-backed artifacts used by
the daily report, log center, and failure-analysis pages.

The operation is idempotent for the records owned by this script: rerunning it
updates the same demo records instead of deleting user data or truncating
tables.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

repository_root = Path(__file__).resolve().parents[1]
application_root = repository_root / "backend"
sys.path.insert(0, str(application_root))
sys.path.insert(0, str(repository_root))

from database.bootstrap import create_tables_with_latest_schema
from infrastructure.core.config import settings
from infrastructure.core.security import hash_password
from infrastructure.db.base import SessionLocal, engine
from infrastructure.persistence.models import (
    AlertCondition,
    AlertConditionGroup,
    AlertHistory,
    AlertRule,
    AnalysisMemory,
    AppLog,
    CIJob,
    CIResult,
    CodeComplexityDetail,
    CodeDuplicationDetail,
    CodeMetricsFileHeatmap,
    CodeMetricsSnapshot,
    CodeSecurityDetail,
    DailyFailureRecord,
    DailyReportHistory,
    FeatureCompatibility,
    FeatureUsageLog,
    FailureAnnotation,
    IssueDiagnosisHistory,
    JobFailureAnalysis,
    JobOwner,
    KubernetesClusterConfig,
    LLMProviderConfig,
    ModelConfig,
    ModelFeatureMatrix,
    ModelFoMapping,
    ModelRegistry,
    ModelReport,
    ModelSyncConfig,
    NightlyTestCase,
    PerformanceData,
    ProjectDashboardConfig,
    PullRequest,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
    SchedulerHeartbeat,
    TestCase,
    TestRun,
    TestSuiteSnapshot,
    User,
    UserLoginLog,
    WorkflowConfig,
    Contributor,
    ContributorMetrics,
    CommitClassification,
    ErrorRecord,
    ImageBuildPlan,
    QuadrantSnapshot,
)
from infrastructure.storage.failure_analysis_file_store import FailureAnalysisFileStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_local_demo")

DEMO_MARKER = "local-demo-v1"
DEMO_PREFIX = "[local-demo]"

# Contributor board mock data constants
random.seed(42)

_CB_MODULES = ["csrc", "attention", "MoE", "EPLB", "MRV2", "decoder", "scheduler", "kv_cache", "tokenizer", "config"]
_CB_COMMIT_TYPES = ["BugFix", "Feature", "Performance", "Doc", "Test", "Refactor", "CI", "Misc"]
_CB_COMMIT_TYPE_WEIGHTS = [30, 25, 10, 15, 8, 5, 3, 4]

_CB_COMMIT_MESSAGES = {
    "BugFix": [
        "fix(attention): correct kernel launch config for A5",
        "fix(kv_cache): handle edge case in cache eviction",
        "fix: resolve OOM on 910B with large batch",
        "fix(MoE): balance expert load across NPU",
        "fix: correct dtype handling for w8a8 models",
    ],
    "Feature": [
        "feat(EPLB): add speculative decoding support",
        "feat: support Qwen3-235B on A5",
        "feat(scheduler): implement dynamic batch sizing",
        "feat: add FP8 quantization pipeline",
        "feat(MRV2): implement new attention backend",
    ],
    "Performance": [
        "perf(attention): optimize 310P memory copy",
        "perf: reduce kernel launch overhead by 15%",
        "perf(kv_cache): use shared memory for small blocks",
        "perf: batch tokenization for high-throughput",
    ],
    "Doc": [
        "docs: add A5 hardware setup guide",
        "docs: update performance benchmark results",
        "docs: fix typo in README",
    ],
    "Test": [
        "test: add e2e test for MoE routing",
        "test(ci): add 310P nightly coverage",
        "test: improve flaky test reliability",
    ],
    "Refactor": [
        "refactor(scheduler): simplify batch allocation logic",
        "refactor: extract common kernel utilities",
    ],
    "CI": [
        "ci: update runner image to v2024.10",
        "ci: add 910C testing stage",
    ],
    "Misc": [
        "chore: update dependency versions",
        "misc: cleanup unused imports",
    ],
}

_CB_BUILD_TYPES = ["full_build", "minimal_build", "nightly_only", "skip", "smart_check", "conservative"]
_CB_BUILD_WEIGHTS = [25, 20, 15, 15, 15, 10]

_CB_CONTRIBUTORS = [
    # INCENTIVE - Star contributors
    {"login": "ykato", "name": "Yushi Kato", "github_id": 1001, "is_community": True, "company": "Sony", "quadrant": "INCENTIVE"},
    {"login": "adam", "name": "Adam", "github_id": 1002, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "xwjiang", "name": "Xiangwang Jiang", "github_id": 1003, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "hjiang", "name": "Hexiang Jiang", "github_id": 1004, "is_community": True, "company": "Stanford", "quadrant": "INCENTIVE"},
    {"login": "david", "name": "David Wu", "github_id": 1005, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    # INCENTIVE - Active contributors
    {"login": "jiasenli", "name": "Jiachen Lin", "github_id": 1006, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "michael", "name": "Michael Zhang", "github_id": 1007, "is_community": True, "company": "NVIDIA", "quadrant": "INCENTIVE"},
    {"login": "alicechen", "name": "Alice Chen", "github_id": 1008, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "bobwang", "name": "Bob Wang", "github_id": 1009, "is_community": False, "company": "Huawei", "quadrant": "INCENTIVE"},
    {"login": "carolliu", "name": "Carol Liu", "github_id": 1010, "is_community": True, "company": "Tsinghua", "quadrant": "INCENTIVE"},
    # IMPROVE - High contrib, high error
    {"login": "erranddev1", "name": "Errand Dev 1", "github_id": 1011, "is_community": True, "company": "Community", "quadrant": "IMPROVE"},
    {"login": "erranddev2", "name": "Errand Dev 2", "github_id": 1012, "is_community": True, "company": "Alibaba", "quadrant": "IMPROVE"},
    {"login": "erranddev3", "name": "Errand Dev 3", "github_id": 1013, "is_community": True, "company": "Baidu", "quadrant": "IMPROVE"},
    {"login": "erranddev4", "name": "Errand Dev 4", "github_id": 1014, "is_community": True, "company": "Tencent", "quadrant": "IMPROVE"},
    # ENCOURAGE - Low contrib, low error
    {"login": "newbie1", "name": "Newbie Dev 1", "github_id": 1015, "is_community": True, "company": "Student", "quadrant": "ENCOURAGE"},
    {"login": "newbie2", "name": "Newbie Dev 2", "github_id": 1016, "is_community": True, "company": "Community", "quadrant": "ENCOURAGE"},
    {"login": "newbie3", "name": "Newbie Dev 3", "github_id": 1017, "is_community": True, "company": "Student", "quadrant": "ENCOURAGE"},
    {"login": "newbie4", "name": "Newbie Dev 4", "github_id": 1018, "is_community": True, "company": "Community", "quadrant": "ENCOURAGE"},
    # REPLACE - Low contrib, high error
    {"login": "toxicdev1", "name": "Toxic Dev 1", "github_id": 1019, "is_community": True, "company": "External", "quadrant": "REPLACE"},
    {"login": "toxicdev2", "name": "Toxic Dev 2", "github_id": 1020, "is_community": True, "company": "External", "quadrant": "REPLACE"},
]


def _cb_detect_roles(merged, reviews, approvals, merges, additions):
    roles = []
    if merged > 0 or additions > 0:
        roles.append("developer")
    if reviews >= 3:
        roles.append("reviewer")
    if merges >= 5:
        roles.append("maintainer")
    if not roles:
        roles.append("developer")
    return roles


def _cb_generate_sha():
    return uuid.uuid4().hex[:40]


def _cb_monthly_metrics(quadrant):
    profiles = {
        "INCENTIVE": {"pr": (5, 15), "code": (500, 3000), "review": (10, 40), "approval": (5, 20), "merge": (3, 15), "error": (0, 1), "core": (2, 8)},
        "IMPROVE": {"pr": (5, 12), "code": (400, 2000), "review": (5, 20), "approval": (2, 10), "merge": (1, 5), "error": (3, 8), "core": (1, 5)},
        "ENCOURAGE": {"pr": (0, 3), "code": (0, 200), "review": (0, 5), "approval": (0, 2), "merge": (0, 1), "error": (0, 1), "core": (0, 2)},
        "REPLACE": {"pr": (0, 3), "code": (0, 200), "review": (0, 3), "approval": (0, 1), "merge": (0, 1), "error": (4, 10), "core": (0, 1)},
    }
    p = profiles[quadrant]
    today = date.today()
    metrics = []
    for i in range(6, 0, -1):
        end = min(today, today.replace(day=1) - timedelta(days=(i - 1) * 30))
        start = end - timedelta(days=29)
        pr = random.randint(*p["pr"])
        code = random.randint(*p["code"])
        review = random.randint(*p["review"])
        approval = random.randint(*p["approval"])
        merge = random.randint(*p["merge"])
        error = round(random.uniform(*p["error"]), 2)
        core = random.randint(*p["core"])
        score = round(pr * 10.0 + code * 0.5 + review * 3.0 + approval * 5.0 + core * 8.0, 2)
        metrics.append({
            "period_start": start,
            "period_end": end,
            "merged_pr_count": pr,
            "code_additions": code,
            "code_deletions": random.randint(0, code // 2),
            "review_count": review,
            "approval_count": approval,
            "merge_count": merge,
            "core_module_count": core,
            "closed_pr_count": random.randint(0, 2),
            "contribution_score": score,
            "error_score": error,
            "quadrant": quadrant,
        })
    return metrics


async def seed_contributor_board(db: Any, now: datetime) -> None:
    """Seed contributor board mock data into the existing database."""
    today = date.today()
    six_months_ago = today - timedelta(days=180)
    contributor_map: dict[str, Contributor] = {}

    # 1) Create contributors
    for c in _CB_CONTRIBUTORS:
        existing = await db.execute(select(Contributor).where(Contributor.github_id == c["github_id"]))
        row = existing.scalar_one_or_none()
        if row:
            contributor_map[c["login"]] = row
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
    logger.info("  contributor_board: %d contributors", len(contributor_map))

    # 2) Metrics + CommitClassifications
    metrics_count = 0
    commits_count = 0
    for c_info in _CB_CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]
        for m in _cb_monthly_metrics(c_info["quadrant"]):
            existing = await db.execute(
                select(ContributorMetrics).where(
                    ContributorMetrics.contributor_login == login,
                    ContributorMetrics.period_start == m["period_start"],
                    ContributorMetrics.period_end == m["period_end"],
                )
            )
            if existing.scalar_one_or_none():
                continue
            db.add(ContributorMetrics(
                contributor_id=contrib.id,
                contributor_login=login,
                **m,
            ))
            metrics_count += 1

            # Generate commit classifications
            for _ in range(max(1, m["merged_pr_count"])):
                ctype = random.choices(_CB_COMMIT_TYPES, weights=_CB_COMMIT_TYPE_WEIGHTS, k=1)[0]
                is_chip = random.random() < 0.15
                db.add(CommitClassification(
                    commit_sha=_cb_generate_sha(),
                    commit_message=random.choice(_CB_COMMIT_MESSAGES.get(ctype, ["misc"])),
                    commit_type=ctype,
                    confidence=round(random.uniform(0.8, 1.0), 2),
                    modules_touched=random.sample(_CB_MODULES, k=random.randint(1, 3)),
                    files_changed_count=random.randint(1, 20),
                    is_chip_specific=is_chip,
                    chip_target=random.choice(["910B", "910C", "310P", "A5"]) if is_chip else None,
                    contributor_login=login,
                    pr_number=random.randint(1, 500),
                    repo_name='vllm-ascend',
                    created_at=datetime.combine(
                        m["period_start"] + timedelta(days=random.randint(0, 28)),
                        datetime.min.time(),
                        tzinfo=UTC,
                    ),
                ))
                commits_count += 1

    await db.flush()
    logger.info("  contributor_board: %d metrics, %d classifications", metrics_count, commits_count)

    # 3) ErrorRecords
    error_count = 0
    for c_info in _CB_CONTRIBUTORS:
        if c_info["quadrant"] not in ("IMPROVE", "REPLACE"):
            continue
        for _ in range(random.randint(2, 6 if c_info["quadrant"] == "IMPROVE" else 8)):
            db.add(ErrorRecord(
                contributor_login=c_info["login"],
                error_type="revert",
                related_pr_number=random.randint(1, 500),
                related_commit_sha=_cb_generate_sha(),
                revert_count=1,
                ci_failure_count=random.randint(0, 2),
                nightly_failure_count=random.randint(0, 1),
                weight_score=5.0,
                description=f'Revert "{random.choice(_CB_COMMIT_MESSAGES["Feature"])}"',
            ))
            error_count += 1
    logger.info("  contributor_board: %d error records", error_count)

    # 4) BuildPlans
    bp_count = 0
    for c_info in _CB_CONTRIBUTORS:
        for _ in range(random.randint(3, 8)):
            db.add(ImageBuildPlan(
                pr_number=random.randint(1, 500),
                commit_sha=_cb_generate_sha(),
                commit_type=random.choice(_CB_COMMIT_TYPES),
                build_type=random.choices(_CB_BUILD_TYPES, weights=_CB_BUILD_WEIGHTS, k=1)[0],
                chips=random.sample(["910B", "910C", "310P", "A5"], k=random.randint(1, 2)),
                os_list=["ubuntu22.04"],
                cache_strategy=random.choice(["layer_cache", "full_rebuild", "smart_cache"]),
                estimated_saving_percent=round(random.uniform(0, 80), 1),
                actual_execution=random.random() < 0.7,
                repo_name='vllm-ascend',
                created_at=now - timedelta(days=random.randint(1, 180)),
            ))
            bp_count += 1
    logger.info("  contributor_board: %d build plans", bp_count)

    # 5) Update role_types
    for c_info in _CB_CONTRIBUTORS:
        login = c_info["login"]
        contrib = contributor_map[login]
        result = await db.execute(
            select(
                func.sum(ContributorMetrics.merged_pr_count),
                func.sum(ContributorMetrics.review_count),
                func.sum(ContributorMetrics.approval_count),
                func.sum(ContributorMetrics.merge_count),
                func.sum(ContributorMetrics.code_additions),
            ).where(ContributorMetrics.contributor_login == login)
        )
        row = result.one()
        contrib.role_types = _cb_detect_roles(
            int(row[0] or 0), int(row[1] or 0), int(row[2] or 0),
            int(row[3] or 0), int(row[4] or 0),
        )

    logger.info("  contributor_board: role_types updated for %d contributors", len(_CB_CONTRIBUTORS))


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def upsert(
    db: Any,
    model: Any,
    filters: dict[str, Any],
    values: dict[str, Any],
) -> Any:
    """Update an owned record or insert it when it does not exist."""
    result = await db.execute(select(model).filter_by(**filters).limit(1))
    item = result.scalar_one_or_none()
    if item is None:
        item = model(**filters, **values)
        db.add(item)
        await db.flush()
        return item
    for key, value in values.items():
        setattr(item, key, value)
    await db.flush()
    return item


async def seed_users(db: Any, now: datetime) -> dict[str, User]:
    users: dict[str, User] = {}
    specs = [
        ("admin", "admin@vllm-ascend.local", "super_admin", "admin123"),
        ("manager", "manager@vllm-ascend.local", "admin", "manager123"),
        ("user", "user@vllm-ascend.local", "user", "user123"),
    ]
    for username, email, role, password in specs:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(user)
            await db.flush()
        users[username] = user
    return users


MODEL_SPECS = [
    {
        "name": "Qwen/Qwen2.5-7B-Instruct",
        "display": "Qwen2.5 7B Instruct",
        "series": "Qwen",
        "fo": "qwen2.5-7b",
        "tier": "standard",
        "status": "supported",
        "hardware": ["A2", "A3", "310P"],
    },
    {
        "name": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "display": "DeepSeek R1 Distill Qwen 7B",
        "series": "DeepSeek",
        "fo": "deepseek-r1-distill-qwen-7b",
        "tier": "standard",
        "status": "supported",
        "hardware": ["A2", "A3"],
    },
    {
        "name": "meta-llama/Llama-3.1-8B-Instruct",
        "display": "Llama 3.1 8B Instruct",
        "series": "Llama",
        "fo": "llama3.1-8b",
        "tier": "experimental",
        "status": "experimental",
        "hardware": ["A3", "310P"],
    },
    {
        "name": "Qwen/Qwen2.5-VL-7B-Instruct",
        "display": "Qwen2.5-VL 7B Instruct",
        "series": "Qwen",
        "fo": "qwen2.5-vl-7b",
        "tier": "preview",
        "status": "untested",
        "hardware": ["A3"],
    },
]


async def seed_models(
    db: Any,
    admin: User,
    now: datetime,
) -> tuple[dict[str, ModelConfig], dict[str, ModelRegistry]]:
    configs: dict[str, ModelConfig] = {}
    registries: dict[str, ModelRegistry] = {}
    features = ["prefix_caching", "chunked_prefill", "speculative_decoding", "multimodal"]

    for index, spec in enumerate(MODEL_SPECS):
        config = await upsert(
            db,
            ModelConfig,
            {"model_name": spec["name"]},
            {
                "series": spec["series"],
                "config_yaml": f"model: {spec['name']}\nmax_model_len: 8192\n",
                "status": "active",
                "created_by": admin.id,
                "key_metrics_config": json_text(
                    {"throughput": {"target": 80}, "ttft_ms": {"target": 150}}
                ),
                "pass_threshold": json_text({"pass_rate": 0.95, "latency_p95_ms": 250}),
                "startup_commands": json_text(
                    {"mix": f"vllm serve {spec['name']} --max-model-len 8192"}
                ),
                "official_doc_url": "https://docs.vllm.ai/",
                "updated_at": now,
            },
        )
        configs[spec["name"]] = config

        registry = await upsert(
            db,
            ModelRegistry,
            {"model_name": spec["name"], "role": "generative"},
            {
                "display_name": spec["display"],
                "series": spec["series"],
                "model_type": "text_generative",
                "tier": spec["tier"],
                "support_status": spec["status"],
                "weight_formats": ["fp16", "w8a8"],
                "kv_cache_types": ["fp16", "int8"],
                "supported_hardware": spec["hardware"],
                "max_model_len": "8192",
                "note": f"{DEMO_PREFIX} local support-matrix fixture",
                "official_doc_url": "https://docs.vllm.ai/",
                "config_yaml": config.config_yaml,
                "startup_commands": {"mix": f"vllm serve {spec['name']}"},
                "key_metrics_config": {"throughput": {"target": 80}},
                "pass_threshold": {"pass_rate": 0.95},
                "first_supported_version": "0.8.0",
                "manual_overrides": {},
                "status": "active",
                "created_by": admin.id,
                "source": "manual",
                "upstream_synced_at": now,
                "updated_at": now,
            },
        )
        registries[spec["name"]] = registry

        for feature_index, feature in enumerate(features):
            status = (
                "supported"
                if feature_index < 2 or spec["status"] == "supported"
                else "experimental"
                if feature == "speculative_decoding"
                else "untested"
            )
            await upsert(
                db,
                ModelFeatureMatrix,
                {"model_id": registry.id, "feature_key": feature},
                {
                    "feature_status": status,
                    "hardware_scope": spec["hardware"],
                    "note": f"{DEMO_PREFIX} generated fixture",
                    "verified_by_report": status == "supported",
                    "updated_at": now,
                },
            )

        await upsert(
            db,
            ModelFoMapping,
            {"model_key": spec["name"]},
            {"model_fo": spec["fo"], "updated_at": now},
        )

    compatibility = [
        ("prefix_caching", "chunked_prefill", "compatible", "常用组合"),
        ("prefix_caching", "speculative_decoding", "compatible", "吞吐优化组合"),
        ("multimodal", "speculative_decoding", "unknown", "依模型版本验证"),
        ("chunked_prefill", "multimodal", "compatible", "视觉模型示例"),
    ]
    for feature_a, feature_b, status, footnote in compatibility:
        await upsert(
            db,
            FeatureCompatibility,
            {"feature_a": feature_a, "feature_b": feature_b},
            {"compatibility": status, "footnote": f"{DEMO_PREFIX} {footnote}", "synced_at": now},
        )

    return configs, registries


async def seed_workflows(
    db: Any,
    now: datetime,
) -> list[WorkflowConfig]:
    specs = [
        ("Nightly-A2", "schedule_nightly_test_a2.yaml", "A2", "schedule"),
        ("Nightly-A3", "schedule_nightly_test_a3.yaml", "A3", "schedule"),
        ("Nightly-A5", "schedule_nightly_test_a5.yaml", "A5", "workflow_dispatch"),
        ("PR-Validation", "pull_request_validation.yaml", "A3", "pull_request"),
    ]
    workflows: list[WorkflowConfig] = []
    for order, (name, workflow_file, hardware, event) in enumerate(specs):
        workflows.append(
            await upsert(
                db,
                WorkflowConfig,
                {"workflow_name": name},
                {
                    "workflow_file": workflow_file,
                    "hardware": hardware,
                    "event": event,
                "description": f"{DEMO_PREFIX} {hardware} development workflow",
                "enabled": True,
                "display_order": order,
                "stats_start_hour": 8,
                "stats_end_hour": 8,
                "last_sync_at": now - timedelta(minutes=12),
                "updated_at": now,
            },
            )
        )
    return workflows


async def seed_ci(
    db: Any,
    workflows: list[WorkflowConfig],
    now: datetime,
) -> tuple[list[CIResult], list[CIJob]]:
    results: list[CIResult] = []
    jobs: list[CIJob] = []
    job_names = [
        "Qwen2.5-7B nightly benchmark",
        "DeepSeek-R1 compatibility suite",
        "A5 nightly validation suite",
        "Llama-3.1 serving regression",
    ]
    for day_offset in range(9, -1, -1):
        for workflow_index, workflow in enumerate(workflows):
            run_id = 970000 + day_offset * 10 + workflow_index
            started = now - timedelta(days=day_offset, hours=2 + workflow_index)
            # Keep demo builds visually representative: completed Workflow Runs
            # range from 20 minutes to 8 hours instead of clustering under 20 minutes.
            duration_minutes = [20, 35, 50, 75, 100, 140, 180, 240, 320, 480][
                (day_offset * len(workflows) + workflow_index) % 10
            ]
            duration = duration_minutes * 60
            failed = (day_offset + workflow_index) % 5 == 0
            in_progress = day_offset == 0 and workflow_index == len(workflows) - 1
            conclusion = None if in_progress else "failure" if failed else "success"
            result = await upsert(
                db,
                CIResult,
                {"run_id": run_id},
                {
                    "workflow_name": workflow.workflow_name,
                    "run_number": 410 - day_offset * 3 + workflow_index,
                    "status": "in_progress" if in_progress else "completed",
                    "conclusion": conclusion,
                    "event": workflow.event,
                    "branch": "main" if workflow.event in ("schedule", "workflow_dispatch") else "feature/local-demo",
                    "head_sha": f"demo{run_id:036d}"[-40:],
                    "started_at": started,
                    "completed_at": None if in_progress else started + timedelta(seconds=duration),
                    "duration_seconds": None if in_progress else duration,
                    "hardware": workflow.hardware,
                    "data": json_text(
                        {
                            "id": run_id,
                            "name": workflow.workflow_name,
                            "head_branch": "main",
                            "run_attempt": 1,
                            "local_demo": True,
                        }
                    ),
                    "updated_at": now,
                },
            )
            results.append(result)

            job_id = 980000 + day_offset * 10 + workflow_index
            job_name = job_names[workflow_index]
            job_duration = duration - 90
            job = await upsert(
                db,
                CIJob,
                {"job_id": job_id},
                {
                    "run_id": run_id,
                    "workflow_name": workflow.workflow_name,
                    "job_name": job_name,
                    "status": "in_progress" if in_progress else "completed",
                    "conclusion": conclusion,
                    "started_at": started + timedelta(seconds=30),
                    "completed_at": None
                    if in_progress
                    else started + timedelta(seconds=30 + job_duration),
                    "duration_seconds": None if in_progress else job_duration,
                    "hardware": workflow.hardware,
                    "runner_name": f"runner-{workflow.hardware.lower()}-demo",
                    "runner_labels": json_text(["self-hosted", workflow.hardware, "local-demo"]),
                    "steps_data": json_text(
                        [
                            {"name": "Prepare environment", "conclusion": "success"},
                            {
                                "name": "Run benchmark",
                                "conclusion": "failure" if failed else "success",
                            },
                            {"name": "Upload report", "conclusion": "skipped" if failed else "success"},
                        ]
                    ),
                    "logs_url": f"https://github.com/vllm-project/vllm-ascend/actions/runs/{run_id}",
                    "data": json_text({"local_demo": True, "runner_group": "development"}),
                    "processing_status": "处理中" if failed and day_offset < 3 else "未处理",
                    "updated_at": now,
                },
            )
            jobs.append(job)
    return results, jobs


async def seed_nightly_and_failures(
    db: Any,
    workflows: list[WorkflowConfig],
    jobs: list[CIJob],
    now: datetime,
) -> None:
    today = now.date()
    nightly_jobs = [
        ("Nightly-A2", "Qwen2.5-7B nightly benchmark", "Qwen/Qwen2.5-7B-Instruct", "qwen2.5-7b", "A2", "alice"),
        ("Nightly-A3", "DeepSeek-R1 compatibility suite", "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B", "deepseek-r1-distill-qwen-7b", "A3", "bob"),
        ("Nightly-A5", "A5 nightly validation suite", "Qwen/Qwen2.5-7B-Instruct", "qwen2.5-7b", "A5", "avery"),
        ("PR-Validation", "Llama-3.1 serving regression", "meta-llama/Llama-3.1-8B-Instruct", "llama3.1-8b", "A3", "charlie"),
    ]
    for offset in range(4):
        report_date = today - timedelta(days=offset)
        for workflow_name, job_name, model, fo, hardware, owner in nightly_jobs:
            await upsert(
                db,
                NightlyTestCase,
                {
                    "report_date": report_date,
                    "source_branch": "main",
                    "workflow_name": workflow_name,
                    "job_name": job_name,
                },
                {
                    "display_name": f"{model.split('/')[-1]} nightly",
                    "test_model": model,
                    "model_fo": fo,
                    "owner": owner,
                    "deployment_type": "single-node",
                    "notes": f"{DEMO_PREFIX} nightly snapshot",
                    "enabled": True,
                    "updated_at": now,
                },
            )

        if offset < 3:
            workflow_name, job_name, model, fo, hardware, _owner = nightly_jobs[(offset + 1) % len(nightly_jobs)]
            job = next(
                item
                for item in jobs
                if item.workflow_name == workflow_name and item.job_name == job_name
            )
            processing_status = ["未处理", "处理中", "已修复"][offset]
            await upsert(
                db,
                DailyFailureRecord,
                {
                    "report_date": report_date,
                    "source_branch": "main",
                    "workflow_name": workflow_name,
                    "job_name": job_name,
                },
                {
                    "run_id": job.run_id,
                    "job_id": job.job_id,
                    "conclusion": "failure",
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                    "duration_seconds": job.duration_seconds,
                    "hardware": hardware,
                    "display_name": f"{model.split('/')[-1]} nightly",
                    "test_model": model,
                    "model_fo": fo,
                    "owner": "alice",
                    "deployment_type": "single-node",
                    "processing_status": processing_status,
                    "problem_category": ["环境问题", "代码问题", "测试用例问题"][offset],
                    "related_pr": str(120 + offset),
                    "notes": f"{DEMO_PREFIX} failure record",
                    "updated_by": "admin",
                    "status_updated_at": now - timedelta(days=offset),
                    "github_job_url": f"https://github.com/vllm-project/vllm-ascend/actions/runs/{job.run_id}",
                    "updated_at": now,
                },
            )

    for workflow_name, job_name, owner in [
        ("Nightly-A2", "Qwen2.5-7B nightly benchmark", "Alice Zhang"),
        ("Nightly-A3", "DeepSeek-R1 compatibility suite", "Bob Li"),
        ("Nightly-A5", "A5 nightly validation suite", "Avery Chen"),
        ("PR-Validation", "Llama-3.1 serving regression", "Charlie Wang"),
    ]:
        await upsert(
            db,
            JobOwner,
            {"workflow_name": workflow_name, "job_name": job_name},
            {
                "display_name": job_name,
                "owner": owner,
                "email": f"{owner.split()[0].lower()}@example.com",
                "notes": f"{DEMO_PREFIX} owner mapping",
                "is_hidden": False,
                "updated_at": now,
            },
        )


async def seed_model_reports(
    db: Any,
    configs: dict[str, ModelConfig],
    registries: dict[str, ModelRegistry],
    now: datetime,
) -> None:
    for model_index, spec in enumerate(MODEL_SPECS):
        model_name = spec["name"]
        for day_offset in range(3):
            created_at = now - timedelta(days=day_offset, hours=model_index)
            passed = (model_index + day_offset) % 4 != 3
            report_key = f"{DEMO_MARKER}:{model_index}:{day_offset}"
            await upsert(
                db,
                ModelReport,
                {"workflow_run_id": 960000 + model_index * 10 + day_offset},
                {
                    "model_config_id": configs[model_name].id,
                    "model_registry_id": registries[model_name].id,
                    "report_json": {
                        "seed_key": report_key,
                        "model_name": model_name,
                        "summary": "local development report",
                        "metrics": {"throughput": 84 - model_index * 7, "latency_p95_ms": 170 + day_offset * 15},
                    },
                    "pass_fail": "pass" if passed else "fail",
                    "auto_pass_fail": "pass" if passed else "fail",
                    "manual_override": False,
                    "metrics_json": {
                        "throughput": 84 - model_index * 7 - day_offset,
                        "ttft_ms": 112 + model_index * 8,
                        "latency_p95_ms": 170 + day_offset * 15,
                    },
                    "created_at": created_at,
                    "report_markdown": f"# {model_name}\n\n{DEMO_PREFIX} report {report_key}\n",
                    "vllm_version": "0.8.5",
                    "hardware": spec["hardware"][0],
                    "dtype": "fp16",
                    "features": ["prefix_caching", "chunked_prefill"],
                    "serve_cmd": {"mix": f"vllm serve {model_name}"},
                    "environment": {"ASCEND_RT_VISIBLE_DEVICES": "0,1"},
                    "tasks": [{"name": "throughput", "target": 80, "actual": 84 - model_index * 7}],
                },
            )


async def seed_performance(
    db: Any,
    now: datetime,
) -> None:
    for model_index, spec in enumerate(MODEL_SPECS[:3]):
        for hardware_index, hardware in enumerate(["A2", "A3"]):
            for sample in range(4):
                timestamp = now - timedelta(days=sample, hours=model_index + hardware_index)
                test_name = f"{DEMO_PREFIX}/{spec['name']}/serving/{sample}"
                await upsert(
                    db,
                    PerformanceData,
                    {
                        "test_name": test_name,
                        "hardware": hardware,
                        "model_name": spec["name"],
                    },
                    {
                        "vllm_version": "0.8.5",
                        "vllm_commit": f"vllm-demo-{sample:04d}",
                        "vllm_ascend_commit": f"ascend-demo-{sample:04d}",
                        "test_type": "serving",
                        "metrics_json": json_text(
                            {
                                "throughput_tokens_per_second": 72 + model_index * 8 + hardware_index * 5 + sample,
                                "ttft_ms": 120 + model_index * 12 + sample * 4,
                                "e2e_latency_p95_ms": 210 + sample * 10,
                            }
                        ),
                        "timestamp": timestamp,
                    },
                )


async def seed_pull_requests(
    db: Any,
    now: datetime,
) -> None:
    owner = settings.GITHUB_OWNER
    repo = settings.GITHUB_REPO
    stages = ["submitted", "reviewing", "approved", "ci_running", "ci_passed", "ci_failed", "merging", "merged", "closed"]
    for index, stage in enumerate(stages):
        created_at = now - timedelta(days=10 - index)
        merged = stage == "merged"
        closed = stage == "closed"
        state = "merged" if merged else "closed" if closed else "open"
        ci_status = "success" if stage in {"ci_passed", "merging", "merged"} else "failure" if stage == "ci_failed" else "pending"
        await upsert(
            db,
            PullRequest,
            {"pr_number": 120 + index, "owner": owner, "repo": repo},
            {
                "title": f"{DEMO_PREFIX} improve {['scheduler', 'resource dashboard', 'model support'][index % 3]} flow",
                "author": ["alice", "bob", "charlie"][index % 3],
                "author_avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
                "author_email": f"author{index}@example.com",
                "html_url": f"https://github.com/{owner}/{repo}/pull/{120 + index}",
                "state": state,
                "is_draft": stage == "submitted",
                "labels": ["enhancement", "local-demo"] if index % 2 == 0 else ["bug"],
                "head_branch": f"demo/feature-{index}",
                "head_sha": f"pr-demo-{index:034d}"[-40:],
                "base_branch": "main",
                "additions": 48 + index * 13,
                "deletions": 8 + index * 3,
                "changed_files": 2 + index % 5,
                "pipeline_stage": stage,
                "review_status": "approved" if stage in {"approved", "ci_running", "ci_passed", "ci_failed", "merging", "merged"} else "pending",
                "reviewers": ["reviewer-a", "reviewer-b"],
                "ci_status": ci_status,
                "ci_workflow_run_id": 970000 + index,
                "first_review_at": created_at + timedelta(hours=5) if index > 0 else None,
                "first_approved_at": created_at + timedelta(days=1) if stage in {"approved", "ci_running", "ci_passed", "ci_failed", "merging", "merged"} else None,
                "ci_started_at": created_at + timedelta(days=1, hours=2) if index > 2 else None,
                "ci_completed_at": created_at + timedelta(days=1, hours=3) if stage in {"ci_passed", "ci_failed", "merging", "merged"} else None,
                "merged_at": created_at + timedelta(days=2) if merged else None,
                "closed_at": created_at + timedelta(days=2) if closed else None,
                "created_at": created_at,
                "updated_at": now,
                "data": {"local_demo": True, "milestone": "development"},
            },
        )


async def seed_clusters_and_resources(
    db: Any,
    admin: User,
    now: datetime,
) -> dict[str, KubernetesClusterConfig]:
    clusters: dict[str, KubernetesClusterConfig] = {}
    cluster_specs = [
        ("dev-a2", "A2 development cluster", "A2", 16, 11.5),
        ("dev-a3", "A3 staging-like cluster", "A3", 32, 24.0),
    ]
    for cluster_index, (name, description, hardware, total, used) in enumerate(cluster_specs):
        cluster = await upsert(
            db,
            KubernetesClusterConfig,
            {"name": name},
            {
                "description": f"{DEMO_PREFIX} {description}",
                "kubeconfig_encrypted": "local-demo-kubeconfig",
                "context": f"{name}-context",
                "default_label_selector": "environment=development",
                "namespaces": "vllm-project,default",
                "npu_resource_name": "huawei.com/Ascend910",
                "enabled": True,
                "display_order": cluster_index,
                "created_by": admin.id,
                "updated_at": now,
            },
        )
        clusters[name] = cluster

        for sample in range(9):
            collected_at = now - timedelta(hours=sample * 3)
            current_used = min(total, used + ((sample + cluster_index) % 4) - 1.0)
            await upsert(
                db,
                ResourceNpuMetrics,
                {"cluster_id": cluster.id, "collected_at": collected_at},
                {
                    "cluster_name": name,
                    "npu_total": total,
                    "npu_used": current_used,
                    "npu_available": total - current_used,
                    "npu_utilization": round(current_used / total * 100, 2),
                    "executing_pods_count": 4 + sample % 3,
                    "pr_count": 2 + sample % 4,
                    "top_pods_json": [
                        {
                            "name": f"demo-{hardware.lower()}-pod-{pod_index}",
                            "namespace": "vllm-project",
                            "phase": "Running",
                            "npu": 2 + pod_index,
                            "pr_number": 120 + pod_index,
                        }
                        for pod_index in range(3)
                    ],
                },
            )

            for node_index in range(2):
                node_name = f"{name}-node-{node_index + 1}"
                cpu_total = 96 if hardware == "A2" else 128
                cpu_used = 37 + cluster_index * 10 + node_index * 5 + sample % 4
                npu_total = total / 2
                npu_used = current_used / 2 + node_index
                await upsert(
                    db,
                    ResourceNodeMetrics,
                    {
                        "cluster_id": cluster.id,
                        "node_name": node_name,
                        "collected_at": collected_at,
                    },
                    {
                        "cluster_name": name,
                        "cpu_cores_total": cpu_total,
                        "cpu_cores_used": cpu_used,
                        "cpu_cores_available": cpu_total - cpu_used,
                        "cpu_utilization": round(cpu_used / cpu_total * 100, 2),
                        "memory_bytes_total": 512 * 1024**3,
                        "memory_bytes_used": (240 + node_index * 35 + sample * 2) * 1024**3,
                        "memory_bytes_available": (272 - node_index * 35 - sample * 2) * 1024**3,
                        "memory_utilization": 48 + node_index * 8 + sample,
                        "npu_total": npu_total,
                        "npu_used": npu_used,
                        "npu_available": npu_total - npu_used,
                        "npu_utilization": round(npu_used / npu_total * 100, 2),
                        "executing_pods_count": 2 + node_index,
                    },
                )
    return clusters


async def seed_alerts(
    db: Any,
    admin: User,
    clusters: dict[str, KubernetesClusterConfig],
    now: datetime,
) -> None:
    cluster = clusters["dev-a3"]
    rule = await upsert(
        db,
        AlertRule,
        {"user_id": admin.id, "name": f"{DEMO_PREFIX} NPU utilization high"},
        {
            "cluster_id": cluster.id,
            "node_name": None,
            "enabled": True,
            "notify_email": False,
            "notification_email": admin.email,
            "last_triggered_at": now - timedelta(hours=2),
            "updated_at": now,
        },
    )
    group = await upsert(
        db,
        AlertConditionGroup,
        {"rule_id": rule.id, "display_order": 0},
        {"logic": "AND"},
    )
    await upsert(
        db,
        AlertCondition,
        {"group_id": group.id, "metric_field": "npu_utilization", "display_order": 0},
        {"operator": ">", "threshold": 80.0, "is_exclude": False},
    )
    await upsert(
        db,
        AlertHistory,
        {"rule_id": rule.id, "triggered_at": now - timedelta(hours=2)},
        {
            "rule_name": rule.name,
            "actual_value": 86.4,
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "node_name": f"{cluster.name}-node-2",
            "condition_details": {"npu_utilization": {"actual": 86.4, "threshold": 80}},
            "notification_sent": False,
            "notification_error": "local demo notification disabled",
        },
    )


async def seed_failure_analysis(
    db: Any,
    jobs: list[CIJob],
    now: datetime,
) -> None:
    store = FailureAnalysisFileStore()
    failed_jobs = [job for job in jobs if job.conclusion == "failure"][:2]
    for index, job in enumerate(failed_jobs):
        report_path = await store.save_report(
            job.workflow_name,
            job.job_name,
            int(job.job_id),
            (
                f"# Failure analysis: {job.job_name}\n\n"
                f"- Source: `{DEMO_MARKER}`\n"
                f"- Run: `{job.run_id}`\n"
                f"- Category: {'environment' if index == 0 else 'code'}\n\n"
                "## Root cause\n\n"
                "The local fixture simulates a reproducible CI failure so the "
                "analysis detail page can be inspected without an LLM call.\n\n"
                "## Suggested action\n\n"
                "Review the runner allocation and rerun the affected benchmark.\n"
            ),
        )
        await upsert(
            db,
            JobFailureAnalysis,
            {"job_id": job.job_id},
            {
                "run_id": job.run_id,
                "workflow_name": job.workflow_name,
                "job_name": job.job_name,
                "failure_date": job.started_at or now,
                "failure_fingerprint": f"demo-fingerprint-{index:08d}",
                "problem_category": "environment" if index == 0 else "code",
                "root_cause_summary": "Local demo failure for detail-page verification",
                "improvement_measures_summary": "Inspect runner and rerun the benchmark",
                "report_file_path": report_path,
                "pdf_file_path": None,
                "llm_provider": "local-demo",
                "llm_model": "fixture",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "generation_time_seconds": 0.1,
                "analysis_status": "completed",
                "analysis_phase": "validated",
                "evidence_ledger": [{"source": "local-demo", "confidence": 1.0}],
                "validation_result": {"valid": True, "message": "fixture report"},
                "agent_trace": [{"step": "seed", "status": "completed"}],
                "agent_steps": 1,
                "triggered_by": "manual",
                "share_token": f"local-demo-share-{index}",
                "updated_at": now,
            },
        )


async def seed_test_board(
    db: Any,
    jobs: list[CIJob],
    now: datetime,
) -> None:
    cases: list[TestCase] = []
    case_specs = [
        ("test_prefix_cache", "serving", "performance", "A3"),
        ("test_chunked_prefill", "serving", "performance", "A3"),
        ("test_quantization_w8a8", "compatibility", "nightly", "A2"),
        ("test_multimodal_inputs", "multimodal", "nightly", "A3"),
        ("test_scheduler_recovery", "e2e", "e2e-full", "A2"),
    ]
    for index, (test_name, suite, category, hardware) in enumerate(case_specs):
        case = await upsert(
            db,
            TestCase,
            {
                "test_name": f"{DEMO_PREFIX}/{test_name}",
                "test_suite": suite,
                "hardware": hardware,
            },
            {
                "module_name": "tests.local_demo",
                "test_type": "e2e" if suite == "e2e" else "benchmark",
                "category": category,
                "card_count": 2 if hardware == "A2" else 4,
                "file_path": f"tests/{suite}/{test_name}.py",
                "class_name": "LocalDemoSuite",
                "test_name_hash": f"localdemo{index:024d}"[-32:],
                "owner": ["alice", "bob", "charlie"][index % 3],
                "owner_email": "qa@example.com",
                "inference_confidence": 1.0,
                "data_granularity": "file_level",
                "is_flaky": index == 3,
                "flaky_rate": 0.12 if index == 3 else 0.01,
                "flaky_evidence_count": 4 if index == 3 else 0,
                "flip_count_30d": 2 if index == 3 else 0,
                "pass_rate_7d": 0.86 if index == 3 else 0.96,
                "pass_rate_30d": 0.91 if index == 3 else 0.98,
                "avg_duration_seconds": 54 + index * 11,
                "duration_p90_seconds": 82 + index * 15,
                "last_pass_duration_seconds": 49 + index * 10,
                "health_score": 74 if index == 3 else 94 - index * 3,
                "health_level": "C" if index == 3 else "A" if index < 2 else "B",
                "first_seen_at": now - timedelta(days=45),
                "last_seen_at": now,
                "last_result": "failed" if index == 3 else "passed",
                "last_run_at": now - timedelta(hours=index),
                "total_runs": 24,
                "total_passed": 21 if index == 3 else 23,
                "total_failed": 3 if index == 3 else 1,
                "lifetime_runs": 72,
                "lifetime_failures": 7 if index == 3 else 3,
                "issues_found": 1 if index == 3 else 0,
                "auto_issues_found": 1 if index == 3 else 0,
                "updated_at": now,
            },
        )
        cases.append(case)

    for case_index, case in enumerate(cases):
        for run_index in range(6):
            started = now - timedelta(days=run_index, hours=case_index)
            failed = case_index == 3 and run_index in {1, 4}
            result = "failed" if failed else "passed" if run_index != 5 else "skipped"
            job = jobs[(case_index + run_index) % len(jobs)]
            run = await upsert(
                db,
                TestRun,
                {"test_case_id": case.id, "started_at": started},
                {
                    "ci_job_id": job.job_id,
                    "ci_run_id": job.run_id,
                    "workflow_name": job.workflow_name,
                    "job_name": job.job_name,
                    "result": result,
                    "duration_seconds": 48 + case_index * 8 + run_index,
                    "model_load_seconds": 11 + case_index,
                    "test_exec_seconds": 37 + run_index,
                    "failure_category": "environment" if failed else None,
                    "failure_message": "simulated runner timeout" if failed else None,
                    "flip_detected": failed and run_index == 4,
                    "head_sha": f"test-demo-{case_index}{run_index:038d}"[-40:],
                    "event": "schedule",
                    "branch": "main",
                    "completed_at": started + timedelta(seconds=52 + case_index * 8),
                },
            )
            if failed:
                await upsert(
                    db,
                    FailureAnnotation,
                    {"test_run_id": run.id, "annotated_category": "environment"},
                    {
                        "annotated_by": "admin",
                        "annotation_source": "manual",
                    },
                )

    for snapshot_index in range(4):
        snapshot_date = now.date() - timedelta(days=snapshot_index)
        await upsert(
            db,
            TestSuiteSnapshot,
            {
                "suite_name": "local-demo-suite",
                "hardware": "A3",
                "card_count": 4,
                "snapshot_date": snapshot_date.isoformat(),
            },
            {
                "test_type": "benchmark",
                "total_cases": 5,
                "passed_cases": 4 if snapshot_index == 1 else 5,
                "failed_cases": 1 if snapshot_index == 1 else 0,
                "skipped_cases": 0,
                "flaky_cases": 1,
                "pass_rate": 0.8 if snapshot_index == 1 else 1.0,
                "health_score": 82 if snapshot_index == 1 else 96,
                "health_level": "B" if snapshot_index == 1 else "A",
                "avg_duration_seconds": 62 + snapshot_index,
                "duration_p50_seconds": 55 + snapshot_index,
                "duration_p90_seconds": 90 + snapshot_index,
                "total_duration_seconds": 310 + snapshot_index * 5,
                "failure_by_category": {"environment": 1} if snapshot_index == 1 else {},
            },
        )


async def seed_code_metrics(
    db: Any,
    now: datetime,
) -> None:
    previous_snapshot: CodeMetricsSnapshot | None = None
    for snapshot_index in range(3):
        snapshot = await upsert(
            db,
            CodeMetricsSnapshot,
            {
                "snapshot_date": now.date() - timedelta(days=snapshot_index),
                "repo": "vllm-ascend",
                "branch": "main",
            },
            {
                "tag": f"v0.8.{5 - snapshot_index}",
                "collection_status": "complete",
                "collection_duration_seconds": 42,
                "total_loc": 112000 + snapshot_index * 2400,
                "total_raw_lines": 151000 + snapshot_index * 2500,
                "loc_python": 56000 + snapshot_index * 1200,
                "loc_cpp": 41000 + snapshot_index * 900,
                "loc_c": 7000,
                "loc_cmake": 3200,
                "loc_shell": 1800,
                "total_functions": 4200 + snapshot_index * 80,
                "total_files": 620 + snapshot_index * 9,
                "cc_total": 7900 + snapshot_index * 120,
                "cc_per_method": 4.8 + snapshot_index * 0.1,
                "cc_maximum": 38 + snapshot_index,
                "cc_huge_count": 21 + snapshot_index,
                "cc_huge_ratio": 0.012,
                "cc_adequacy": 0.88,
                "max_depth": 9,
                "depth_huge_count": 12,
                "depth_huge_ratio": 0.008,
                "method_lines_total": 124000,
                "lines_per_method": 29.5,
                "huge_method_count": 38,
                "huge_method_ratio": 0.009,
                "huge_file_count": 6,
                "huge_headerfile_count": 3,
                "dup_blocks": 92,
                "dup_lines": 1480,
                "dup_ratio": 0.013,
                "unsafe_functions_count": 4,
                "warning_suppression_count": 15,
                "lint_errors": 2 if snapshot_index == 1 else 0,
                "lint_warnings": 18 + snapshot_index,
                "todo_count": 86,
                "fixme_count": 12,
                "hack_count": 3,
                "health_score": 91 - snapshot_index * 2,
                "health_score_complexity": 89,
                "health_score_security": 94,
                "health_score_duplication": 92,
                "health_score_method_size": 90,
                "health_score_tech_debt": 86,
                "health_score_lint": 95,
                "module_loc": {"vllm_ascend": 68000, "csrc": 26000, "tests": 18000},
                "language_loc": {"Python": 56000, "C++": 41000, "C": 7000, "Shell": 1800},
            },
        )
        previous_snapshot = snapshot

    if previous_snapshot is None:
        return
    detail_specs = [
        (
            CodeComplexityDetail,
            {"snapshot_id": previous_snapshot.id, "file_path": "vllm_ascend/worker.py", "function_name": "execute_model"},
            {"language": "Python", "cyclomatic_complexity": 24, "max_nesting_depth": 6, "function_lines": 136, "start_line": 210},
        ),
        (
            CodeDuplicationDetail,
            {"snapshot_id": previous_snapshot.id, "file_a": "vllm_ascend/runner.py", "file_b": "tests/test_runner.py"},
            {"lines": 38, "token_count": 260, "fragment": "local demo duplicated fragment"},
        ),
        (
            CodeSecurityDetail,
            {"snapshot_id": previous_snapshot.id, "file_path": "api/v1/system_config.py", "line_number": 188},
            {"severity": "warning", "tool": "ruff", "rule_id": "S105", "message": "Demo security finding for inspection"},
        ),
    ]
    for model, filters, values in detail_specs:
        await upsert(db, model, filters, values)

    for index, file_path in enumerate(
        ["vllm_ascend/worker.py", "vllm_ascend/runner.py", "api/v1/ci.py", "tests/test_runner.py"]
    ):
        await upsert(
            db,
            CodeMetricsFileHeatmap,
            {"repo": "vllm-ascend", "file_path": file_path},
            {
                "change_count": 19 - index * 3,
                "bug_fix_count": 4 - index % 2,
                "last_changed": now - timedelta(days=index),
                "last_commit_sha": f"heatmap-demo-{index:033d}"[-40:],
            },
        )


async def seed_system_records(
    db: Any,
    users: dict[str, User],
    now: datetime,
) -> None:
    admin = users["admin"]
    for index in range(6):
        timestamp = now - timedelta(hours=index * 3)
        await upsert(
            db,
            AppLog,
            {"timestamp": timestamp.isoformat(), "message": f"{DEMO_PREFIX} application event {index}"},
            {
                "level": "WARNING" if index == 2 else "INFO",
                "module": "local_demo",
                "function_name": "seed_local_demo",
                "line_number": 100 + index,
                "traceback": "Demo traceback: no real failure" if index == 2 else None,
            },
        )
        await upsert(
            db,
            UserLoginLog,
            {"user_id": admin.id, "login_time": timestamp},
            {
                "ip_address": "127.0.0.1",
                "ip_address_hashed": "local-demo-ip",
                "user_agent": "local-demo-browser",
                "login_method": "password",
            },
        )
        await upsert(
            db,
            FeatureUsageLog,
            {"user_id": admin.id, "access_time": timestamp, "request_path": f"/api/v1/local-demo/{index}"},
            {
                "feature_name": ["dashboard", "ci", "resources", "test-board"][index % 4],
                "metadata_json": {"source": DEMO_MARKER},
            },
        )

    await upsert(
        db,
        IssueDiagnosisHistory,
        {"diagnosis_type": "ci_job", "target_id": "980000"},
        {
            "user_id": admin.id,
            "target_label": "Qwen2.5-7B nightly benchmark",
            "report_content": f"{DEMO_PREFIX} diagnosis report for local inspection",
            "model_used": "fixture",
            "duration_seconds": 0.2,
            "status": "success",
            "is_liked": True,
            "like_count": 2,
        },
    )

    await upsert(
        db,
        AnalysisMemory,
        {"memory_type": "failure_analysis", "title": f"{DEMO_PREFIX} runner timeout"},
        {
            "source_id": 980000,
            "content": "A local fixture memory entry for keyword search.",
            "tags": ["runner", "timeout", "local-demo"],
            "metadata_": {"workflow_name": "Nightly-A2", "category": "environment"},
            "summary": "Runner timeout fixture",
            "status": "active",
        },
    )

    for offset in range(3):
        report_date = (now.date() - timedelta(days=offset)).isoformat()
        await upsert(
            db,
            DailyReportHistory,
            {"report_date": report_date},
            {
                "recipients": "local@example.com",
                "subject": f"{DEMO_PREFIX} daily report {report_date}",
                "status": "sent" if offset else "pending",
                "sent_at": None if offset == 0 else now - timedelta(days=offset),
                "ci_summary": {"success": 7, "failure": 1},
                "model_summary": {"pass": 10, "fail": 2},
                "github_summary": {"pull_requests": 4, "issues": 2, "commits": 12},
                "performance_summary": {"throughput_delta": "+4.2%"},
                "ai_report_content": f"# {DEMO_PREFIX} Daily report\n\nData date: {report_date}",
            },
        )

    config_values = {
        "model_support_matrix": {
            "features": ["prefix_caching", "chunked_prefill", "speculative_decoding", "multimodal"],
            "models": [
                {"model": spec["name"], "hardware": spec["hardware"], "status": spec["status"]}
                for spec in MODEL_SPECS
            ],
        },
        "force_merge_records": [],
        "daily_summary_schedule": {"enabled": True, "cron_hour": 8, "cron_minute": 0, "timezone": "Asia/Shanghai"},
        "daily_summary_projects": [
            {"project": "vllm-ascend", "owner": settings.GITHUB_OWNER, "repo": settings.GITHUB_REPO, "enabled": True}
        ],
        "daily_report_config": {"enabled": True, "recipients": ["local@example.com"], "send_hour": 8},
        "smtp_config": {
            "smtp_host": "",
            "smtp_port": 25,
            "smtp_username": "",
            "smtp_password": "",
            "smtp_use_tls": False,
            "from_email": "local@example.com",
        },
        "resource_metrics_config": {"interval_minutes": 1, "retention_days": 30},
        "failure_analysis_agent_config": {"runtime": "fixture", "max_turns": 1, "timeout_seconds": 5},
        "support_matrix_sync_status": {"status": "local-demo", "last_sync_at": now.isoformat()},
        "support_matrix_global_features": ["prefix_caching", "chunked_prefill", "speculative_decoding", "multimodal"],
    }
    for key, value in config_values.items():
        await upsert(
            db,
            ProjectDashboardConfig,
            {"config_key": key},
            {"config_value": value, "description": f"{DEMO_PREFIX} runtime configuration"},
        )

    for provider, display_name, model in [
        ("openai", "OpenAI", "gpt-4o-mini"),
        ("anthropic", "Anthropic", "claude-3-5-sonnet"),
        ("dashscope", "通义千问", "qwen-plus"),
    ]:
        await upsert(
            db,
            LLMProviderConfig,
            {"provider": provider},
            {
                "display_name": display_name,
                "api_key": None,
                "api_base_url": None,
                "default_model": model,
                "enabled": False,
                "is_active": False,
                "display_order": 0,
            },
        )

    for index, workflow in enumerate(["Nightly-A2", "Nightly-A3", "PR-Validation"]):
        await upsert(
            db,
            ModelSyncConfig,
            {"workflow_file": f"local_demo_model_sync_{index}.yaml"},
            {
                "workflow_name": workflow,
                "artifacts_pattern": "model-report-*",
                "file_patterns": json_text(["results/*.yaml", "metrics/*.json"]),
                "branch": "main",
                "enabled": True,
                "last_sync_at": now - timedelta(hours=index + 1),
            },
        )

    await upsert(
        db,
        SchedulerHeartbeat,
        {"id": 1},
        {
            "running": True,
            "jobs": {
                "ci-sync": {"name": "CI sync", "next_run": (now + timedelta(minutes=18)).isoformat()},
                "daily-summary": {"name": "Daily summary", "next_run": (now + timedelta(hours=8)).isoformat()},
            },
            "pid": os.getpid(),
            "updated_at": now,
        },
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def seed_file_artifacts(now: datetime) -> None:
    data_dir = Path(settings.DATA_DIR)
    today = now.date()
    projects = ["vllm-ascend", "vllm"]
    for project_index, project in enumerate(projects):
        for day_offset in range(2):
            data_date = today - timedelta(days=day_offset)
            prs = [
                {
                    "number": 120 + project_index * 10 + day_offset,
                    "title": f"{DEMO_PREFIX} {project} integration update",
                    "state": "open",
                    "user": {"login": "alice"},
                    "html_url": f"https://github.com/{settings.GITHUB_OWNER}/{project}/pull/{120 + day_offset}",
                    "labels": [{"name": "enhancement"}],
                    "created_at": (now - timedelta(days=day_offset + 1)).isoformat(),
                    "merged_at": None,
                }
            ]
            issues = [
                {
                    "number": 800 + project_index * 10 + day_offset,
                    "title": f"{DEMO_PREFIX} investigate nightly signal",
                    "state": "open" if day_offset == 0 else "closed",
                    "user": {"login": "bob"},
                    "html_url": f"https://github.com/{settings.GITHUB_OWNER}/{project}/issues/{800 + day_offset}",
                    "labels": [{"name": "bug"}],
                    "created_at": (now - timedelta(days=day_offset + 2)).isoformat(),
                    "closed_at": None if day_offset == 0 else now.isoformat(),
                }
            ]
            commits = [
                {
                    "sha": f"daily-demo-{project_index}{day_offset:038d}"[-40:],
                    "short_sha": f"demo{project_index}{day_offset}",
                    "message": f"{DEMO_PREFIX} refresh local dashboard fixture",
                    "author": {"name": "charlie", "email": "charlie@example.com"},
                    "commit": {"author": {"date": (now - timedelta(hours=day_offset * 4)).isoformat()}},
                    "html_url": "https://github.com/vllm-project/vllm-ascend/commit/demo",
                }
            ]
            payload = {
                "project": project,
                "data_date": data_date.isoformat(),
                "fetched_at": now.isoformat(),
                "pull_requests": prs,
                "issues": issues,
                "commits": commits,
                "counts": {"pull_requests": len(prs), "issues": len(issues), "commits": len(commits)},
            }
            write_json(data_dir / "daily-data" / project / f"{data_date.isoformat()}.json", payload)
            summary_dir = data_dir / "daily-data" / project / "summaries"
            summary_dir.mkdir(parents=True, exist_ok=True)
            summary_path = summary_dir / f"{data_date.isoformat()}.md"
            summary_path.write_text(
                f"# {project} daily summary\n\n"
                f"{DEMO_PREFIX} summary for {data_date.isoformat()}.\n\n"
                f"- Pull requests: {len(prs)}\n"
                f"- Issues: {len(issues)}\n"
                f"- Commits: {len(commits)}\n",
                encoding="utf-8",
            )
            write_json(
                summary_dir / f"{data_date.isoformat()}.meta.json",
                {
                    "project": project,
                    "data_date": data_date.isoformat(),
                    "has_data": True,
                    "llm_provider": "fixture",
                    "llm_model": "local-demo",
                    "generated_at": now.isoformat(),
                },
            )

    log_dir = data_dir / "claude_logs" / today.isoformat()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "local-demo-session.log").write_text(
        (
            "Provider: fixture\n"
            "Model: local-demo\n"
            "Route: local\n"
            "Duration: 0.2s\n"
            "Exit Code: 0\n"
            "--- STDOUT ---\n"
            "Generated a local demo report successfully.\n"
            "--- STDERR ---\n"
            "\n"
        ),
        encoding="utf-8",
    )
    write_json(
        log_dir / "local-demo-session_conversation.json",
        {
            "messages": [
                {"role": "user", "content": "Inspect the local dashboard fixture"},
                {"role": "assistant", "content": "The local demo data is ready."},
            ]
        },
    )


async def seed_database(skip_files: bool) -> None:
    if os.environ.get("ENVIRONMENT", "development").lower() == "production":
        raise RuntimeError("seed_local_demo.py is forbidden in production")

    parsed_url = urlparse(settings.DATABASE_URL)
    logger.info(
        "Seeding local demo data into %s://%s:%s%s",
        parsed_url.scheme,
        parsed_url.hostname or "unknown",
        parsed_url.port or "",
        parsed_url.path,
    )
    await create_tables_with_latest_schema()
    # Keep generated timestamps stable within a UTC hour so repeated seeding
    # during one development session updates the same fixture rows.
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    async with SessionLocal() as db:
        try:
            users = await seed_users(db, now)
            configs, registries = await seed_models(db, users["admin"], now)
            workflows = await seed_workflows(db, now)
            _, jobs = await seed_ci(db, workflows, now)
            await seed_nightly_and_failures(db, workflows, jobs, now)
            await seed_model_reports(db, configs, registries, now)
            await seed_performance(db, now)
            await seed_pull_requests(db, now)
            clusters = await seed_clusters_and_resources(db, users["admin"], now)
            await seed_alerts(db, users["admin"], clusters, now)
            await seed_failure_analysis(db, jobs, now)
            await seed_test_board(db, jobs, now)
            await seed_code_metrics(db, now)
            await seed_system_records(db, users, now)
            await seed_contributor_board(db, now)
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    if not skip_files:
        await seed_file_artifacts(now)

    logger.info("Local demo data is ready.")
    logger.info("Login accounts: admin/admin123, manager/manager123, user/user123")
    logger.info("File artifacts: %s", Path(settings.DATA_DIR).resolve())


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed local-only dashboard demo data")
    parser.add_argument(
        "--skip-files",
        action="store_true",
        help="Only seed MySQL records; do not create file-backed demo artifacts",
    )
    args = parser.parse_args()
    try:
        await seed_database(args.skip_files)
    except SQLAlchemyError:
        logger.exception("Local demo database seeding failed")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
