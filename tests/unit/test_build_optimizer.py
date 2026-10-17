from contributor_board.build_optimizer import evaluate_build_plan
from contributor_board.classifier import CommitType


class TestEvaluateBuildPlan:
    """Tests for evaluate_build_plan() — build strategy selection."""

    def test_full_build_bugfix(self):
        plan = evaluate_build_plan(
            commit_sha="abc123",
            commit_type=CommitType.BUGFIX.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.build_type == "full_build"
        assert len(plan.chips) == 4
        assert plan.estimated_saving_percent == 0.0

    def test_full_build_chip_specific(self):
        plan = evaluate_build_plan(
            commit_sha="abc123",
            commit_type=CommitType.BUGFIX.value,
            is_chip_specific=True,
            chip_target="a5",
        )
        assert plan.build_type == "full_build"
        assert plan.chips == ["a5"]
        assert plan.estimated_saving_percent == 75.0

    def test_skip_ci(self):
        plan = evaluate_build_plan(
            commit_sha="def456",
            commit_type=CommitType.CI.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.build_type == "minimal_build"
        assert plan.estimated_saving_percent == 100.0

    def test_skip_doc(self):
        plan = evaluate_build_plan(
            commit_sha="ghi789",
            commit_type=CommitType.DOC.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.build_type == "skip"
        assert plan.estimated_saving_percent == 100.0

    def test_nightly_test(self):
        plan = evaluate_build_plan(
            commit_sha="jkl012",
            commit_type=CommitType.TEST.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.build_type == "nightly_only"
        assert plan.estimated_saving_percent == 50.0

    def test_refactor_source(self):
        plan = evaluate_build_plan(
            commit_sha="mno345",
            commit_type=CommitType.REFACTOR.value,
            is_chip_specific=False,
            chip_target=None,
            files_changed=["csrc/attention/kernel.py"],
        )
        assert plan.build_type == "full_build"
        assert plan.cache_strategy == "rebuild_csrc_cache"

    def test_refactor_docs(self):
        plan = evaluate_build_plan(
            commit_sha="pqr678",
            commit_type=CommitType.REFACTOR.value,
            is_chip_specific=False,
            chip_target=None,
            files_changed=["docs/README.md"],
        )
        assert plan.build_type == "skip"
        assert plan.estimated_saving_percent == 100.0

    def test_misc_conservative(self):
        plan = evaluate_build_plan(
            commit_sha="stu901",
            commit_type=CommitType.MISCELLANEOUS.value,
            is_chip_specific=False,
            chip_target=None,
            files_changed=["some_random_file.txt"],
        )
        assert plan.build_type == "conservative"
        assert plan.estimated_saving_percent == 0.0

    def test_feature_chip_specific(self):
        plan = evaluate_build_plan(
            commit_sha="vwx234",
            commit_type=CommitType.FEATURE.value,
            is_chip_specific=True,
            chip_target="a3",
        )
        assert plan.build_type == "full_build"
        assert plan.chips == ["a3"]
        assert plan.estimated_saving_percent == 75.0

    def test_revert_full_build(self):
        plan = evaluate_build_plan(
            commit_sha="yza567",
            commit_type=CommitType.REVERT.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.build_type == "full_build"
        assert len(plan.chips) == 4

    def test_performance_full_build(self):
        plan = evaluate_build_plan(
            commit_sha="bcd890",
            commit_type=CommitType.PERFORMANCE.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.build_type == "full_build"
        assert len(plan.chips) == 4

    def test_pr_number_propagation(self):
        plan = evaluate_build_plan(
            commit_sha="efg111",
            commit_type=CommitType.DOC.value,
            is_chip_specific=False,
            chip_target=None,
            pr_number=42,
        )
        assert plan.pr_number == 42

    def test_commit_sha_propagation(self):
        plan = evaluate_build_plan(
            commit_sha="deadbeef",
            commit_type=CommitType.CI.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.commit_sha == "deadbeef"

    def test_misc_no_files_conservative(self):
        plan = evaluate_build_plan(
            commit_sha="hhhh22",
            commit_type=CommitType.MISCELLANEOUS.value,
            is_chip_specific=False,
            chip_target=None,
            files_changed=None,
        )
        assert plan.build_type == "conservative"
        assert len(plan.chips) == 4

    def test_nightly_only_cache_reuse(self):
        plan = evaluate_build_plan(
            commit_sha="nnnn33",
            commit_type=CommitType.TEST.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert plan.cache_strategy == "reuse_csrc_cache"

    def test_refactor_vllm_ascend_full_build(self):
        plan = evaluate_build_plan(
            commit_sha="vvvv44",
            commit_type=CommitType.REFACTOR.value,
            is_chip_specific=False,
            chip_target=None,
            files_changed=["vllm_ascend/attention/mm.py"],
        )
        assert plan.build_type == "full_build"

    def test_reasoning_contains_commit_type(self):
        plan = evaluate_build_plan(
            commit_sha="rrrr55",
            commit_type=CommitType.BUGFIX.value,
            is_chip_specific=False,
            chip_target=None,
        )
        assert "BugFix" in plan.reasoning

    def test_chip_specific_reasoning(self):
        plan = evaluate_build_plan(
            commit_sha="cccc66",
            commit_type=CommitType.FEATURE.value,
            is_chip_specific=True,
            chip_target="310p",
        )
        assert "310p" in plan.reasoning
