from contributor_board.classifier import (
    CommitType,
    classify_commit,
)


class TestClassifyCommit:
    """Tests for classify_commit() — prefix matching."""

    def test_classify_bugfix_prefix(self):
        result = classify_commit("[BugFix] fix attention memory leak")
        assert result.commit_type == CommitType.BUGFIX
        assert result.confidence == 0.95

    def test_classify_feature_prefix(self):
        result = classify_commit("[Feature] support EPLB scheduling")
        assert result.commit_type == CommitType.FEATURE
        assert result.confidence == 0.95

    def test_classify_performance_prefix(self):
        result = classify_commit("[Performance] optimize kernel launch")
        assert result.commit_type == CommitType.PERFORMANCE
        assert result.confidence == 0.95

    def test_classify_revert_prefix(self):
        result = classify_commit('[Revert] revert last commit')
        assert result.commit_type == CommitType.REVERT
        assert result.confidence == 0.95

    def test_classify_ci_prefix(self):
        result = classify_commit("[CI] update workflow config")
        assert result.commit_type == CommitType.CI
        assert result.confidence == 0.95

    def test_classify_doc_prefix(self):
        result = classify_commit("[Doc] update README")
        assert result.commit_type == CommitType.DOC
        assert result.confidence == 0.95

    def test_classify_test_prefix(self):
        result = classify_commit("[Test] add e2e test for MoE")
        assert result.commit_type == CommitType.TEST
        assert result.confidence == 0.95

    def test_classify_refactor_prefix(self):
        result = classify_commit("[Refactor] clean up kernel dispatch")
        assert result.commit_type == CommitType.REFACTOR
        assert result.confidence == 0.95

    def test_classify_fix_prefix(self):
        result = classify_commit("[Fix] crash during model load")
        assert result.commit_type == CommitType.BUGFIX
        assert result.confidence == 0.95

    def test_classify_feat_prefix(self):
        result = classify_commit("[Feat] add MoE support")
        assert result.commit_type == CommitType.FEATURE
        assert result.confidence == 0.95

    def test_classify_perf_prefix(self):
        result = classify_commit("[Perf] reduce latency")
        assert result.commit_type == CommitType.PERFORMANCE
        assert result.confidence == 0.95


class TestClassifyCommitKeywords:
    """Tests for classify_commit() — keyword matching."""

    def test_classify_bugfix_keyword(self):
        result = classify_commit("fix memory leak in attention")
        assert result.commit_type == CommitType.BUGFIX
        assert result.confidence == 0.75

    def test_classify_feature_keyword(self):
        result = classify_commit("add support for new model")
        assert result.commit_type == CommitType.FEATURE
        assert result.confidence == 0.75

    def test_classify_performance_keyword(self):
        result = classify_commit("optimize kernel throughput")
        assert result.commit_type == CommitType.PERFORMANCE
        assert result.confidence == 0.75

    def test_classify_revert_keyword(self):
        result = classify_commit("revoke last commit")
        assert result.commit_type == CommitType.REVERT
        assert result.confidence == 0.75

    def test_classify_ci_keyword(self):
        result = classify_commit("update ci pipeline config")
        assert result.commit_type == CommitType.CI
        assert result.confidence == 0.75

    def test_classify_doc_keyword(self):
        result = classify_commit("readme update for getting started")
        assert result.commit_type == CommitType.DOC
        assert result.confidence == 0.75

    def test_classify_test_keyword(self):
        result = classify_commit("update pytest configuration")
        assert result.commit_type == CommitType.TEST
        assert result.confidence == 0.75

    def test_classify_refactor_keyword(self):
        result = classify_commit("refactoring module structure")
        assert result.commit_type == CommitType.REFACTOR
        assert result.confidence == 0.75


class TestClassifyMiscFallback:
    """Tests for classify_commit() — misc fallback."""

    def test_classify_misc_fallback(self):
        result = classify_commit("update something random")
        assert result.commit_type == CommitType.MISCELLANEOUS
        assert result.confidence == 0.50

    def test_classify_misc_empty_message(self):
        result = classify_commit("")
        assert result.commit_type == CommitType.MISCELLANEOUS
        assert result.confidence == 0.50


class TestChipDetection:
    """Tests for classify_commit() — chip-specific detection."""

    def test_chip_detection_310p(self):
        result = classify_commit("适配 310P 芯片")
        assert result.is_chip_specific is True
        assert result.chip_target == "310p"

    def test_chip_detection_a5(self):
        result = classify_commit("A5 chip support")
        assert result.is_chip_specific is True
        assert result.chip_target == "a5"

    def test_chip_detection_a2(self):
        result = classify_commit("Ascend910B compatibility")
        assert result.is_chip_specific is True
        assert result.chip_target == "a2"

    def test_chip_detection_none(self):
        result = classify_commit("普通修复")
        assert result.is_chip_specific is False
        assert result.chip_target is None

    def test_chip_detection_310_no_p(self):
        result = classify_commit("适配 310 推理卡")
        assert result.is_chip_specific is True
        assert result.chip_target == "310p"

    def test_chip_detection_a3(self):
        result = classify_commit("Ascend910C migration")
        assert result.is_chip_specific is True
        assert result.chip_target == "a3"

    def test_chip_detection_950(self):
        result = classify_commit("Ascend950 ready")
        assert result.is_chip_specific is True
        assert result.chip_target == "a5"


class TestCoreModuleDetection:
    """Tests for classify_commit() — core module detection."""

    def test_core_module_detection(self):
        result = classify_commit(
            "fix kernel bug",
            files_changed=["vllm_ascend/attention/xxx.py"],
        )
        assert result.is_core_module is True
        assert "attention" in result.modules_touched

    def test_non_core_module(self):
        result = classify_commit(
            "update readme",
            files_changed=["docs/README.md"],
        )
        assert result.is_core_module is False
        assert result.modules_touched == []

    def test_kernel_module_detection(self):
        result = classify_commit(
            "update kernel",
            files_changed=["csrc/kernel_ops.cu"],
        )
        assert result.is_core_module is True
        assert "kernel" in result.modules_touched

    def test_moe_module_detection(self):
        result = classify_commit(
            "moe improvement",
            files_changed=["vllm_ascend/moe/gating.py"],
        )
        assert result.is_core_module is True
        assert "moe" in result.modules_touched

    def test_eplb_module_detection(self):
        result = classify_commit(
            "eplb fix",
            files_changed=["vllm_ascend/eplb/balance.py"],
        )
        assert result.is_core_module is True
        assert "eplb" in result.modules_touched

    def test_no_files_changed(self):
        result = classify_commit("random commit")
        assert result.is_core_module is False
        assert result.modules_touched == []

    def test_multiple_modules_touched(self):
        result = classify_commit(
            "refactor core",
            files_changed=["vllm_ascend/attention/flash.py", "vllm_ascend/kernel/gemm.py"],
        )
        assert result.is_core_module is True
        assert "attention" in result.modules_touched
        assert "kernel" in result.modules_touched

    def test_a5_module_detection(self):
        result = classify_commit(
            "a5 specific",
            files_changed=["vllm_ascend/a5/launcher.py"],
        )
        assert result.is_core_module is True
        assert "a5" in result.modules_touched
