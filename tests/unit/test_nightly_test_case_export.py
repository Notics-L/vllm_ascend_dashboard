from datetime import date
from types import SimpleNamespace

from api.v1.ci import _nightly_export_model_fo, _serialize_nightly_test_case_export


def test_nightly_test_case_export_matches_import_shape():
    record = SimpleNamespace(
        id=99,
        report_date=date(2026, 8, 24),
        source_branch="releases/v0.26.0rc",
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen)",
        display_name="qwen",
        test_model="Qwen3-32B",
        model_fo="张三",
        owner="李四",
        deployment_type="single-node",
        notes="保留备注",
        enabled=True,
        created_at="not-exported",
        updated_at="not-exported",
    )

    assert _serialize_nightly_test_case_export(record) == {
        "report_date": "2026-08-24",
        "source_branch": "releases/v0.26.0rc",
        "workflow_name": "Nightly-A3",
        "job_name": "single-node (main, qwen)",
        "display_name": "qwen",
        "test_model": "Qwen3-32B",
        "model_fo": "张三",
        "owner": "李四",
        "deployment_type": "single-node",
        "notes": "保留备注",
        "enabled": True,
    }


def test_export_fo_lookup_falls_back_to_normalized_model_mapping():
    snapshot = SimpleNamespace(
        model_fo="",
        test_model="configs/DeepSeek_V31_Terminus_w4a8_w8a8_A5.yaml",
    )
    mapping = {"deepseek_v31_terminus_w4a8_w8a8_a5.yaml": "FO-Owner"}

    assert _nightly_export_model_fo(snapshot, mapping) == "FO-Owner"
