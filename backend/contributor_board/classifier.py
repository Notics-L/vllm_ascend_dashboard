from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import logging

logger = logging.getLogger(__name__)


class CommitType(str, Enum):
    BUGFIX = "BugFix"
    FEATURE = "Feature"
    PERFORMANCE = "Performance"
    REVERT = "Revert"
    CI = "CI"
    DOC = "Doc"
    TEST = "Test"
    REFACTOR = "Refactor"
    MISCELLANEOUS = "Misc"


@dataclass
class ClassificationResult:
    commit_type: CommitType
    confidence: float
    modules_touched: list[str]
    is_chip_specific: bool
    chip_target: str | None
    is_core_module: bool


CHIP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"310[Pp]?"), "310p"),
    (re.compile(r"A5|950|Ascend950"), "a5"),
    (re.compile(r"A2|Ascend910B"), "a2"),
    (re.compile(r"A3|Ascend910C"), "a3"),
]

CORE_MODULES: list[str] = ["attention", "kernel", "moe", "mrv2", "eplb", "a5"]


CLASSIFIERS: dict[CommitType, dict[str, Any]] = {
    CommitType.BUGFIX: {
        "prefixes": ["bugfix", "fix", "bug"],
        "keywords": [
            "fix", "bug", "bugfix", "bug-fix", "patch", "hotfix", "hot-fix",
            "crash", "hang", "deadlock", "timeout", "error", "issue",
            "resolve", "repair", "workaround", "regression", "defect",
        ],
    },
    CommitType.FEATURE: {
        "prefixes": ["feature", "feat", "new"],
        "keywords": [
            "feature", "feat", "add", "implement", "new", "support",
            "enable", "introduce", "addition", "extension",
        ],
    },
    CommitType.PERFORMANCE: {
        "prefixes": ["perf", "performance", "opt"],
        "keywords": [
            "perf", "performance", "optimization", "optimize", "speedup",
            "accelerate", "throughput", "latency", "benchmark", "tune",
            "faster", "efficiency",
        ],
    },
    CommitType.REVERT: {
        "prefixes": ["revert", "rollback"],
        "keywords": [
            "revert", "reverted", "rollback", "roll-back", "undo", "revoke",
        ],
    },
    CommitType.CI: {
        "prefixes": ["ci", "build", "workflow"],
        "keywords": [
            "ci", "cd", "pipeline", "workflow", "jenkins", "github action",
            "github-action", "gitlab", "build system", "makefile", "cmake",
            "docker", "container", "k8s", "kubernetes", "deploy",
        ],
    },
    CommitType.DOC: {
        "prefixes": ["doc", "docs"],
        "keywords": [
            "doc", "docs", "documentation", "readme", "comment", "read-the-docs",
            "tutorial", "example", "changelog",
        ],
    },
    CommitType.TEST: {
        "prefixes": ["test", "tests"],
        "keywords": [
            "test", "tests", "unittest", "pytest", "coverage", "assert",
            "mock", "fixture", "integration test",
        ],
    },
    CommitType.REFACTOR: {
        "prefixes": ["refactor"],
        "keywords": [
            "refactor", "refactoring", "restructure", "cleanup", "clean up",
            "rename", "extract", "merge module", "simplify",
        ],
    },
}


def _classify_by_prefix(message: str) -> CommitType | None:
    stripped = message.strip()
    match = re.match(r"^\[?([^]]+)]?\s*", stripped, re.IGNORECASE)
    if not match:
        return None
    tag = match.group(1).lower()
    for commit_type, rules in CLASSIFIERS.items():
        if tag in rules["prefixes"]:
            return commit_type
    return None


def _classify_by_keywords(message: str) -> CommitType | None:
    lower = message.lower()
    words = re.findall(r"\b\w[\w-]*\b", lower)
    for commit_type, rules in CLASSIFIERS.items():
        for keyword in rules["keywords"]:
            if keyword in words or keyword in lower:
                return commit_type
    return None


def _detect_chip(message: str) -> tuple[bool, str | None]:
    for pattern, chip in CHIP_PATTERNS:
        if pattern.search(message):
            return True, chip
    return False, None


def _detect_modules(files_changed: list[str] | None) -> tuple[list[str], bool]:
    if not files_changed:
        return [], False
    touched: list[str] = []
    is_core = False
    for f in files_changed:
        # Extract top-level directory as module name
        parts = f.split("/")
        if len(parts) > 1:
            top_dir = parts[0]
        else:
            top_dir = "root"
        if top_dir not in touched:
            touched.append(top_dir)
        lower_f = f.lower()
        for mod in CORE_MODULES:
            if mod in lower_f:
                is_core = True
    return touched, is_core


def classify_commit(
    message: str,
    files_changed: list[str] | None = None,
) -> ClassificationResult:
    commit_type = _classify_by_prefix(message)
    confidence = 0.95
    if commit_type is None:
        commit_type = _classify_by_keywords(message)
        confidence = 0.75
    if commit_type is None:
        commit_type = CommitType.MISCELLANEOUS
        confidence = 0.5

    is_chip_specific, chip_target = _detect_chip(message)
    modules_touched, is_core_module = _detect_modules(files_changed)

    return ClassificationResult(
        commit_type=commit_type,
        confidence=confidence,
        modules_touched=modules_touched,
        is_chip_specific=is_chip_specific,
        chip_target=chip_target,
        is_core_module=is_core_module,
    )
