"""
Job 名称解析器 - 支持多种工作流格式的 Job 名称解析为层级结构
"""
import re
from typing import Any


class JobNameParser:
    """统一的 Job 名称解析器"""

    @staticmethod
    def parse(job_name: str, workflow_type: str) -> dict[str, Any]:
        if workflow_type == "nightly_with_base":
            return JobNameParser._parse_nightly_with_base(job_name)
        elif workflow_type == "image_build":
            return JobNameParser._parse_image_build(job_name)
        else:
            return JobNameParser._parse_generic(job_name)

    @staticmethod
    def _parse_nightly_with_base(name: str) -> dict[str, Any]:
        """
        解析 Nightly with base 格式:
        'Build nightly-a2 (ubuntu) / Build base image (a2-ubuntu) / build (...)'
        'Build nightly-a2 (ubuntu) / Build nightly-a2 (ubuntu) / ...'
        """
        if name == "setup-matrix":
            return {
                "group_key": "setup-matrix", "target": "setup-matrix",
                "os_name": "setup-matrix", "phase": "setup",
                "sub_name": "setup-matrix", "architecture": "unknown",
            }

        parts = [p.strip() for p in name.split(" / ")]
        if len(parts) < 2:
            return {
                "group_key": name, "target": name, "os_name": name,
                "phase": "other", "sub_name": name, "architecture": "unknown",
            }

        m = re.match(r"Build nightly-(\w+)\s*\((\w+)\)", parts[0])
        if not m:
            return {
                "group_key": parts[0], "target": parts[0], "os_name": parts[0],
                "phase": "other", "sub_name": name, "architecture": "unknown",
            }

        target = m.group(1)
        os_name = m.group(2)
        group_key = f"a-{target}-{os_name}"

        phase_part = parts[1] if len(parts) >= 2 else ""
        sub_name = parts[-1] if len(parts) >= 2 else name

        if "base image" in phase_part.lower():
            phase = "base"
        elif "merge" in phase_part.lower() or "merge" in sub_name.lower():
            phase = "merge"
        elif "generate" in sub_name.lower() or "matrix" in sub_name.lower():
            phase = "setup"
        else:
            phase = "nightly"

        architecture = "arm64" if "arm" in name.lower() else "amd64"

        return {
            "group_key": group_key, "target": target, "os_name": os_name,
            "phase": phase, "sub_name": sub_name, "architecture": architecture,
        }

    @staticmethod
    def _parse_image_build(name: str) -> dict[str, Any]:
        """
        解析 Image Build and Push 格式:
        'Build and push nightly-a2'
        'A2 Ubuntu / build (linux-aarch64-cpu-4, arm64)'
        """
        m = re.match(r"Build and push (\S+)", name)
        if m:
            tag = m.group(1)
            architecture = "arm64" if "arm" in name.lower() else "amd64"
            return {
                "group_key": tag, "target": tag, "os_name": "",
                "phase": "build", "sub_name": tag, "architecture": architecture,
            }

        if " / " in name:
            parts = name.split(" / ", 1)
            group = parts[0].strip()
            sub = parts[1].strip()
            architecture = "arm64" if "arm" in name.lower() else "amd64"
            return {
                "group_key": group, "target": group, "os_name": "",
                "phase": "build", "sub_name": sub, "architecture": architecture,
            }

        architecture = "arm64" if "arm" in name.lower() else "amd64"
        return {
            "group_key": name, "target": name, "os_name": "",
            "phase": "build", "sub_name": name, "architecture": architecture,
        }

    @staticmethod
    def _parse_generic(name: str) -> dict[str, Any]:
        if " / " in name:
            parts = name.split(" / ", 1)
            group = parts[0].strip()
            sub = parts[1].strip()
            architecture = "arm64" if "arm" in name.lower() else "amd64"
            return {
                "group_key": group, "target": group, "os_name": "",
                "phase": "build", "sub_name": sub, "architecture": architecture,
            }

        architecture = "arm64" if "arm" in name.lower() else "amd64"
        return {
            "group_key": name, "target": name, "os_name": "",
            "phase": "build", "sub_name": name, "architecture": architecture,
        }
