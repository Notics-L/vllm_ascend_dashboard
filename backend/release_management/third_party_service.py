"""
三方件版本管理 - 检测、基线管理、合规检查
"""
import json
import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import (
    ReleaseBuildRun,
    ReleaseThirdPartyBaseline,
    ReleaseThirdPartyVersion,
)

logger = logging.getLogger(__name__)


DETECTION_PATTERNS = {
    "CANN": r"CANN[_\s-]*([dv]?\s*\d+\.\d+(?:\.\d+)?(?:\.[A-Za-z0-9_]+)?)",
    "torch-npu": r"torch-npu[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "torch": r"(?:^|[\s=])torch[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "ascendc": r"ascend-c[_\s-]*(\d+\.\d+(?:\.\d+)?)",
    "numpy": r"numpy[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "protobuf": r"protobuf[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "grpcio": r"grpcio[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "huggingface": r"huggingface[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "transformers": r"transformers[\s=]*(\d+\.\d+(?:\.\d+)?)",
    "accelerate": r"accelerate[\s=]*(\d+\.\d+(?:\.\d+)?)",
}


class ThirdPartyService:
    """三方件版本管理服务"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect_from_logs(
        self, run_id: int, log_content: str
    ) -> list[dict[str, Any]]:
        """从构建日志中检测三方件版本"""
        detected = []
        for component, pattern in DETECTION_PATTERNS.items():
            matches = re.finditer(pattern, log_content, re.IGNORECASE)
            for match in matches:
                version = match.group(1).strip().lstrip("v")
                record = ReleaseThirdPartyVersion(
                    run_id=run_id,
                    component_name=component,
                    component_version=version,
                    component_category=self._guess_category(component),
                    source_type="build_log",
                    source_line=match.group(0),
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

        component_map: dict[str, dict] = {}
        for r in rows:
            name = r.component_name
            if name not in component_map:
                component_map[name] = {
                    "component_name": name,
                    "component_category": r.component_category,
                    "versions": [],
                    "latest_version": r.component_version,
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
