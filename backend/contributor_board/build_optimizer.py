from __future__ import annotations

from dataclasses import dataclass

import logging

from contributor_board.classifier import CommitType

logger = logging.getLogger(__name__)

ALL_CHIPS: list[str] = ["a2", "a3", "310p", "a5"]
ALL_OS: list[str] = ["ubuntu", "openeuler"]

CROS_BUILD_FILES: list[str] = ["csrc/", "vllm_ascend/", "CMakeLists.txt", "cmake/"]
SKIP_BUILD_DIRS: list[str] = ["docs/", "tests/", ".github/"]


@dataclass
class BuildPlan:
    pr_number: int
    commit_sha: str
    commit_type: str
    build_type: str
    chips: list[str]
    os_list: list[str]
    cache_strategy: str
    estimated_saving_percent: float
    reasoning: str


def _match_file_pattern(files_changed: list[str], patterns: list[str]) -> bool:
    for f in files_changed:
        for pattern in patterns:
            if pattern in f:
                return True
    return False


def evaluate_build_plan(
    commit_sha: str,
    commit_type: str,
    is_chip_specific: bool,
    chip_target: str | None,
    files_changed: list[str] | None = None,
    pr_number: int = 0,
) -> BuildPlan:
    if commit_type in (CommitType.BUGFIX.value, CommitType.FEATURE.value,
                        CommitType.PERFORMANCE.value, CommitType.REVERT.value):
        return _build_runtime_change(commit_sha, commit_type, is_chip_specific,
                                     chip_target, pr_number)

    if commit_type == CommitType.CI.value:
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="minimal_build",
            chips=[],
            os_list=[],
            cache_strategy="none",
            estimated_saving_percent=100.0,
            reasoning="CI 类型变更，不影响运行时镜像，跳过构建",
        )

    if commit_type == CommitType.DOC.value:
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="skip",
            chips=[],
            os_list=[],
            cache_strategy="none",
            estimated_saving_percent=100.0,
            reasoning="文档变更，不影响构建产物，跳过构建",
        )

    if commit_type == CommitType.TEST.value:
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="nightly_only",
            chips=ALL_CHIPS,
            os_list=["ubuntu"],
            cache_strategy="reuse_csrc_cache",
            estimated_saving_percent=50.0,
            reasoning="测试用例变更，仅执行 nightly 构建",
        )

    return _build_refactor_or_misc(commit_sha, commit_type, files_changed, pr_number)


def _build_runtime_change(
    commit_sha: str,
    commit_type: str,
    is_chip_specific: bool,
    chip_target: str | None,
    pr_number: int,
) -> BuildPlan:
    if is_chip_specific and chip_target:
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="full_build",
            chips=[chip_target],
            os_list=ALL_OS,
            cache_strategy="reuse_csrc_cache",
            estimated_saving_percent=75.0,
            reasoning=f"{commit_type} 类型变更，仅影响 {chip_target} 芯片，执行针对性构建",
        )

    return BuildPlan(
        pr_number=pr_number,
        commit_sha=commit_sha,
        commit_type=commit_type,
        build_type="full_build",
        chips=ALL_CHIPS,
        os_list=ALL_OS,
        cache_strategy="reuse_csrc_cache",
        estimated_saving_percent=0.0,
        reasoning=f"{commit_type} 类型变更，影响运行时行为，执行全量构建",
    )


def _build_refactor_or_misc(
    commit_sha: str,
    commit_type: str,
    files_changed: list[str] | None,
    pr_number: int,
) -> BuildPlan:
    if not files_changed:
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="conservative",
            chips=ALL_CHIPS,
            os_list=ALL_OS,
            cache_strategy="none",
            estimated_saving_percent=0.0,
            reasoning=f"{commit_type} 类型变更，无法判断影响范围，保守执行全量构建",
        )

    if _match_file_pattern(files_changed, CROS_BUILD_FILES):
        cache_strategy = "rebuild_csrc_cache" if _match_file_pattern(files_changed, ["csrc/"]) else "none"
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="full_build",
            chips=ALL_CHIPS,
            os_list=ALL_OS,
            cache_strategy=cache_strategy,
            estimated_saving_percent=0.0,
            reasoning=f"{commit_type} 类型变更，涉及核心构建文件，执行全量构建",
        )

    if _match_file_pattern(files_changed, SKIP_BUILD_DIRS):
        return BuildPlan(
            pr_number=pr_number,
            commit_sha=commit_sha,
            commit_type=commit_type,
            build_type="skip",
            chips=[],
            os_list=[],
            cache_strategy="none",
            estimated_saving_percent=100.0,
            reasoning=f"{commit_type} 类型变更，仅影响非构建文件，跳过构建",
        )

    return BuildPlan(
        pr_number=pr_number,
        commit_sha=commit_sha,
        commit_type=commit_type,
        build_type="conservative",
        chips=ALL_CHIPS,
        os_list=ALL_OS,
        cache_strategy="none",
        estimated_saving_percent=0.0,
        reasoning=f"{commit_type} 类型变更，影响范围不明确，保守执行全量构建",
    )
