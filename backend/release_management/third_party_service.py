"""
三方件版本管理 - 检测、基线管理、合规检查
"""
import json
import logging
import re
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import (
    ReleaseBuildRun,
    ReleaseThirdPartyBaseline,
    ReleaseThirdPartyVersion,
)

logger = logging.getLogger(__name__)


# 版本号通用后缀：B020, RC1, rc1, master 等
_VER = r"(\d+(?:\.\d+)*[A-Za-z0-9._-]*?)(?=[\s,;)\]]|$)"

DETECTION_PATTERNS: dict[str, dict[str, str]] = {
    # ── vLLM ──
    "vLLM": {
        "pattern": r"(?:^|[\s=:/])vLLM[\s=:_v]+(" + _VER + ")",
        "category": "vllm",
        "repo": "https://github.com/vllm-project/vllm",
    },
    "vLLM Ascend": {
        "pattern": r"vLLM[\s_-]?Ascend[\s=:_]+(?:releases/)?(?:v?)(" + _VER + r")(?:\s+[0-9a-f]{40})?",
        "category": "vllm",
        "repo": "https://github.com/vllm-project/vllm-ascend",
    },
    "torch": {
        "pattern": r"(?:^|[\s=:/])torch[\s=]+(" + _VER + ")",
        "category": "vllm",
        "repo": "https://github.com/pytorch/pytorch",
    },
    "torch-npu": {
        "pattern": r"torch-npu[\s=]+(" + _VER + ")",
        "category": "vllm",
        "repo": "https://github.com/Ascend/pytorch",
    },

    # ── 核心框架（按卡型） ──
    "CANN": {
        "pattern": r"CANN[\s_-]*(" + _VER + ")",
        "category": "core",
        "repo": "https://www.hiascend.com/software/cann",
    },
    "FrameworkPTAdapter": {
        "pattern": r"(?:Framework[\s_-]?PT[\s_-]?Adapter|PT[\s_-]?Adapter)[\s_-]*(" + _VER + ")",
        "category": "core",
        "repo": "",
    },
    "Triton Performance Optimization": {
        "pattern": r"Triton[\s_-]*Performance[\s_-]*Optimization[\s_-]*(" + _VER + ")",
        "category": "core",
        "repo": "",
    },

    # ── 工具 ──
    "Mooncake": {
        "pattern": r"Mooncake[\s_-]*v?(\d+(?:\.\d+)*(?:-rc\d+)?)",
        "category": "tool",
        "repo": "https://github.com/kvcache-ai/Mooncake",
    },
    "memcache": {
        "pattern": r"memcache[\s_-]*(" + _VER + ")",
        "category": "tool",
        "repo": "",
    },
    "memfabric_hybrid": {
        "pattern": r"memfabric[_\s-]*hybrid[\s_-]*(" + _VER + ")",
        "category": "tool",
        "repo": "",
    },
    "AISBench": {
        "pattern": r"AISBench[\s_-]*v?(" + _VER + ")",
        "category": "tool",
        "repo": "https://github.com/AISBench/benchmark",
    },

    # ── 生态仓 ──
    "生态仓": {
        "pattern": r"(?:生态仓|flash[_\s-]*linear[_\s-]*attention[_\s-]*npu[_\s-]*a3)[\s_-]*v?(" + _VER + ")",
        "category": "ecosystem",
        "repo": "https://mirrors.huaweicloud.com/ascend/repos/pypi/flash_linear_attention_npu_a3/",
    },

    # ── 镜像 ──
    "镜像归档": {
        "pattern": r"镜像归档[\s_-]*(\S+)?",
        "category": "image",
        "repo": "",
    },

    # ── 通用依赖（保留） ──
    "numpy": {
        "pattern": r"numpy[\s=]+(" + _VER + ")",
        "category": "library",
    },
    "protobuf": {
        "pattern": r"protobuf[\s=]+(" + _VER + ")",
        "category": "library",
    },
    "grpcio": {
        "pattern": r"grpcio[\s=]+(" + _VER + ")",
        "category": "library",
    },
    "transformers": {
        "pattern": r"transformers[\s=]+(" + _VER + ")",
        "category": "library",
    },
    "accelerate": {
        "pattern": r"accelerate[\s=]+(" + _VER + ")",
        "category": "library",
    },
    "huggingface": {
        "pattern": r"huggingface[\s=]+(" + _VER + ")",
        "category": "library",
    },
    "ascendc": {
        "pattern": r"ascend[-_]?c[\s_-]*(" + _VER + ")",
        "category": "core",
    },
    "docker-image": {
        "pattern": r"(?:docker[\s_-]*image|image[\s_-]*tag)[\s_:]+([\w./:@-]+)",
        "category": "image",
    },
}

CATEGORY_ORDER = ["vllm", "core", "tool", "ecosystem", "image", "library"]
CATEGORY_LABELS = {
    "vllm": "vLLM 框架",
    "core": "核心框架",
    "tool": "工具",
    "ecosystem": "生态仓",
    "image": "镜像",
    "library": "通用依赖",
}


class ThirdPartyService:
    """三方件版本管理服务"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect_from_logs(
        self, run_id: int, log_content: str
    ) -> list[dict[str, Any]]:
        """从构建日志中检测三方件版本"""
        from sqlalchemy import delete as sa_delete

        # Remove old detections for this run (same job may be re-synced)
        # We keep the first detection per (run_id, component_name, component_version)
        await self.db.execute(
            sa_delete(ReleaseThirdPartyVersion).where(
                ReleaseThirdPartyVersion.run_id == run_id
            )
        )
        await self.db.flush()

        # De-duplicate within the same log
        seen: set[tuple[str, str]] = set()
        detected: list[dict[str, Any]] = []

        for component, config in DETECTION_PATTERNS.items():
            pattern = config["pattern"]
            category = config.get("category", self._guess_category(component))
            repo_url = config.get("repo", "")
            matches = re.finditer(pattern, log_content, re.IGNORECASE)
            for match in matches:
                version = match.group(1).strip()
                if not version or version.lower() == "none":
                    continue
                key = (component, version)
                if key in seen:
                    continue
                seen.add(key)

                record = ReleaseThirdPartyVersion(
                    run_id=run_id,
                    component_name=component,
                    component_version=version,
                    component_category=category,
                    source_type="build_log",
                    source_line=match.group(0),
                    repository_url=repo_url or None,
                )
                self.db.add(record)
                detected.append({
                    "component": component,
                    "version": version,
                    "source": match.group(0),
                })

        await self.db.commit()
        return detected

    async def get_compliance_check(
        self, run_id: int
    ) -> dict[str, Any]:
        """检查某次运行的三方件是否符合基线"""
        versions = (await self.db.execute(
            select(ReleaseThirdPartyVersion).where(
                ReleaseThirdPartyVersion.run_id == run_id
            )
        )).scalars().all()

        baselines = (await self.db.execute(
            select(ReleaseThirdPartyBaseline).where(
                ReleaseThirdPartyBaseline.is_active
            )
        )).scalars().all()

        violations = []
        compliant = []

        for v in versions:
            baseline = next(
                (b for b in baselines if b.component_name == v.component_name),
                None,
            )
            if baseline:
                if self._check_compliance(v.component_version, baseline):
                    compliant.append({
                        "component": v.component_name,
                        "version": v.component_version,
                        "status": "compliant",
                    })
                else:
                    violations.append({
                        "component": v.component_name,
                        "version": v.component_version,
                        "expected": json.loads(baseline.approved_versions or "[]")
                            if baseline.approved_versions else None,
                        "min_version": baseline.min_version,
                        "max_version": baseline.max_version,
                    })
            else:
                compliant.append({
                    "component": v.component_name,
                    "version": v.component_version,
                    "status": "no_baseline",
                })

        return {
            "run_id": run_id,
            "compliant": compliant,
            "violations": violations,
            "compliance_rate": round(
                len(compliant) / max(len(compliant) + len(violations), 1) * 100, 1
            ),
        }

    async def get_version_history(
        self, component_name: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """获取指定组件的版本变迁历史"""
        stmt = (
            select(
                ReleaseThirdPartyVersion.component_version,
                ReleaseBuildRun.created_at_remote,
                ReleaseBuildRun.github_run_id,
                ReleaseBuildRun.branch,
            )
            .join(ReleaseBuildRun, ReleaseBuildRun.id == ReleaseThirdPartyVersion.run_id)
            .where(
                ReleaseThirdPartyVersion.component_name == component_name
            )
            .order_by(ReleaseBuildRun.created_at_remote.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [
            {
                "version": r.component_version,
                "run_id": r.github_run_id,
                "branch": r.branch,
                "used_at": r.created_at_remote.isoformat() if r.created_at_remote else "",
            }
            for r in rows
        ]

    async def get_components_overview(self, workflow_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """获取组件版本总览"""
        conditions = [ReleaseThirdPartyVersion.run_id.in_(
            select(ReleaseBuildRun.id).where(ReleaseBuildRun.workflow_id.in_(workflow_ids))
        )] if workflow_ids else []

        stmt = (
            select(
                ReleaseThirdPartyVersion.component_name,
                ReleaseThirdPartyVersion.component_version,
                ReleaseThirdPartyVersion.component_category,
                func.count(ReleaseThirdPartyVersion.id).label("usage_count"),
            )
            .where(*conditions)
            .group_by(
                ReleaseThirdPartyVersion.component_name,
                ReleaseThirdPartyVersion.component_version,
                ReleaseThirdPartyVersion.component_category,
            )
            .order_by(ReleaseThirdPartyVersion.component_name)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        # Load active baselines for status check
        baselines_result = await self.db.execute(
            select(ReleaseThirdPartyBaseline).where(ReleaseThirdPartyBaseline.is_active)
        )
        baseline_map = {b.component_name: b for b in baselines_result.scalars().all()}

        component_map: dict[str, dict] = {}
        for r in rows:
            name = r.component_name
            if name not in component_map:
                baseline = baseline_map.get(name)
                status = "no_baseline"
                if baseline:
                    # Check if the latest version is compliant
                    status = "compliant" if self._check_compliance(r.component_version, baseline) else "violation"

                component_map[name] = {
                    "component_name": name,
                    "component_category": r.component_category,
                    "versions": [],
                    "latest_version": r.component_version,
                    "baseline_status": status,
                }
            component_map[name]["versions"].append({
                "version": r.component_version,
                "usage_count": r.usage_count,
            })

        return list(component_map.values())

    async def list_baselines(self) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(ReleaseThirdPartyBaseline).order_by(
                ReleaseThirdPartyBaseline.component_name
            )
        )
        items = result.scalars().all()
        return [self._baseline_to_dict(b) for b in items]

    async def create_or_update_baseline(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        existing = (await self.db.execute(
            select(ReleaseThirdPartyBaseline).where(
                ReleaseThirdPartyBaseline.component_name == data["component_name"]
            )
        )).scalar_one_or_none()

        if existing:
            existing.component_category = data.get("component_category") or existing.component_category
            existing.approved_versions = json.dumps(data.get("approved_versions", []))
            existing.min_version = data.get("min_version")
            existing.max_version = data.get("max_version")
            existing.notes = data.get("notes")
            existing.is_active = data.get("is_active", True)
        else:
            existing = ReleaseThirdPartyBaseline(
                component_name=data["component_name"],
                component_category=data.get("component_category"),
                approved_versions=json.dumps(data.get("approved_versions", [])),
                min_version=data.get("min_version"),
                max_version=data.get("max_version"),
                notes=data.get("notes"),
                is_active=data.get("is_active", True),
            )
            self.db.add(existing)

        await self.db.commit()
        return self._baseline_to_dict(existing)

    async def delete_baseline(self, baseline_id: int) -> bool:
        bl = await self.db.get(ReleaseThirdPartyBaseline, baseline_id)
        if bl:
            await self.db.delete(bl)
            await self.db.commit()
            return True
        return False

    @staticmethod
    def _check_compliance(version: str, baseline) -> bool:
        approved = json.loads(baseline.approved_versions or "[]")
        if approved and version not in approved:
            return False
        return True

    @staticmethod
    def _guess_category(component: str) -> str:
        framework = ["torch", "torch-npu", "transformers", "accelerate"]
        runtime = ["CANN", "ascendc"]
        library = ["numpy", "protobuf", "grpcio", "huggingface"]

        comp_lower = component.lower()
        if comp_lower in framework:
            return "framework"
        if comp_lower in runtime:
            return "runtime"
        if comp_lower in library:
            return "library"
        return "tool"

    @staticmethod
    def _baseline_to_dict(b) -> dict[str, Any]:
        return {
            "id": b.id,
            "component_name": b.component_name,
            "component_category": b.component_category,
            "approved_versions": json.loads(b.approved_versions or "[]"),
            "min_version": b.min_version,
            "max_version": b.max_version,
            "notes": b.notes,
            "is_active": b.is_active,
            "updated_at": b.updated_at.isoformat() if b.updated_at else None,
        }

    # ─── 版本快照 & 差异对比 ───

    async def list_runs_with_third_party(
        self,
        workflow_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出已同步三方件数据的构建执行列表"""
        from sqlalchemy import exists

        stmt = (
            select(ReleaseBuildRun)
            .where(
                exists().where(
                    ReleaseThirdPartyVersion.run_id == ReleaseBuildRun.id
                )
            )
            .order_by(ReleaseBuildRun.created_at_remote.desc())
            .limit(limit)
        )
        if workflow_id:
            stmt = stmt.where(ReleaseBuildRun.workflow_id == workflow_id)

        result = await self.db.execute(stmt)
        runs = result.scalars().all()

        # 获取 workflow 名称映射
        wf_ids = list({r.workflow_id for r in runs})
        wf_names: dict[int, str] = {}
        if wf_ids:
            from sqlalchemy import select as _sel
            from infrastructure.persistence.models import ReleaseWorkflow
            wf_result = await self.db.execute(
                _sel(ReleaseWorkflow).where(ReleaseWorkflow.id.in_(wf_ids))
            )
            wf_names = {w.id: w.workflow_name for w in wf_result.scalars().all()}

        # 获取每个 run 的组件数量
        run_ids = [r.id for r in runs]
        comp_counts: dict[int, int] = {}
        if run_ids:
            count_result = await self.db.execute(
                select(
                    ReleaseThirdPartyVersion.run_id,
                    func.count(ReleaseThirdPartyVersion.id),
                ).where(ReleaseThirdPartyVersion.run_id.in_(run_ids)).group_by(
                    ReleaseThirdPartyVersion.run_id
                )
            )
            comp_counts = {row[0]: row[1] for row in count_result.all()}

        return [
            {
                "id": r.id,
                "workflow_id": r.workflow_id,
                "workflow_name": wf_names.get(r.workflow_id, f"WF-{r.workflow_id}"),
                "github_run_id": r.github_run_id,
                "status": r.status,
                "branch": r.branch,
                "commit_sha": (r.commit_sha or "")[:8],
                "created_at": r.created_at_remote.isoformat() if r.created_at_remote else None,
                "component_count": comp_counts.get(r.id, 0),
            }
            for r in runs
        ]

    async def get_run_snapshot(self, run_id: int) -> dict[str, Any] | None:
        """获取单次执行的三方件版本快照"""
        run = await self.db.get(ReleaseBuildRun, run_id)
        if not run:
            return None

        versions = (await self.db.execute(
            select(ReleaseThirdPartyVersion).where(
                ReleaseThirdPartyVersion.run_id == run_id
            ).order_by(ReleaseThirdPartyVersion.component_name)
        )).scalars().all()

        # 加载基线
        baselines_result = await self.db.execute(
            select(ReleaseThirdPartyBaseline).where(ReleaseThirdPartyBaseline.is_active)
        )
        baseline_map = {b.component_name: b for b in baselines_result.scalars().all()}

        components: list[dict[str, Any]] = []
        seen: set[str] = set()
        for v in versions:
            key = f"{v.component_name}::{v.component_version}"
            if key in seen:
                continue
            seen.add(key)

            baseline = baseline_map.get(v.component_name)
            baseline_status = "no_baseline"
            approved_versions: list[str] = []
            if baseline:
                approved_versions = json.loads(baseline.approved_versions or "[]")
                baseline_status = "compliant" if self._check_compliance(v.component_version, baseline) else "violation"

            components.append({
                "component_name": v.component_name,
                "component_version": v.component_version,
                "component_category": v.component_category,
                "source_line": v.source_line,
                "repository_url": v.repository_url,
                "baseline_status": baseline_status,
                "approved_versions": approved_versions,
            })

        # 按分类分组
        by_category: dict[str, list[dict[str, Any]]] = {}
        for c in components:
            cat = c["component_category"] or "other"
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(c)

        return {
            "run_id": run.id,
            "github_run_id": run.github_run_id,
            "workflow_id": run.workflow_id,
            "status": run.status,
            "branch": run.branch,
            "commit_sha": run.commit_sha,
            "created_at": run.created_at_remote.isoformat() if run.created_at_remote else None,
            "components": components,
            "components_by_category": by_category,
            "category_order": CATEGORY_ORDER,
            "category_labels": CATEGORY_LABELS,
            "total_components": len(seen),
        }

    async def get_run_diff(self, run_id: int) -> dict[str, Any] | None:
        """对比当前执行与上一次执行的三方件版本差异 + 基线合规"""
        current = await self.get_run_snapshot(run_id)
        if not current:
            return None

        run = await self.db.get(ReleaseBuildRun, run_id)
        if not run:
            return None

        # 查找上一次同 workflow 的执行（排除当前）
        prev_result = await self.db.execute(
            select(ReleaseBuildRun)
            .where(
                ReleaseBuildRun.workflow_id == run.workflow_id,
                ReleaseBuildRun.id != run_id,
                exists().where(
                    ReleaseThirdPartyVersion.run_id == ReleaseBuildRun.id
                ),
            )
            .order_by(ReleaseBuildRun.created_at_remote.desc())
            .limit(1)
        )
        prev_run = prev_result.scalars().first()
        prev_snapshot = await self.get_run_snapshot(prev_run.id) if prev_run else None

        # 构建对比
        prev_versions: dict[str, str] = {}
        if prev_snapshot:
            for c in prev_snapshot["components"]:
                prev_versions[c["component_name"]] = c["component_version"]

        cur_versions: dict[str, str] = {}
        for c in current["components"]:
            cur_versions[c["component_name"]] = c["component_version"]

        all_components = sorted(set(prev_versions) | set(cur_versions))

        diffs: list[dict[str, Any]] = []
        for comp in all_components:
            prev_v = prev_versions.get(comp)
            cur_v = cur_versions.get(comp)
            if prev_v == cur_v:
                change_type = "unchanged"
            elif prev_v is None:
                change_type = "added"
            elif cur_v is None:
                change_type = "removed"
            else:
                change_type = "changed"
            diffs.append({
                "component_name": comp,
                "previous_version": prev_v,
                "current_version": cur_v,
                "change_type": change_type,
            })

        changed = [d for d in diffs if d["change_type"] != "unchanged"]

        return {
            "current_run": {
                "run_id": current["run_id"],
                "github_run_id": current["github_run_id"],
                "branch": current["branch"],
                "commit_sha": current["commit_sha"],
                "created_at": current["created_at"],
            },
            "previous_run": {
                "run_id": prev_run.id,
                "github_run_id": prev_run.github_run_id,
                "branch": prev_run.branch,
                "commit_sha": prev_run.commit_sha,
                "created_at": prev_run.created_at_remote.isoformat() if prev_run and prev_run.created_at_remote else None,
            } if prev_run else None,
            "components": current["components"],
            "diffs": diffs,
            "changed_count": len(changed),
            "unchanged_count": len(diffs) - len(changed),
        }
