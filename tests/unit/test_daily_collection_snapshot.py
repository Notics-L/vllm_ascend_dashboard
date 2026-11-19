"""Execution timestamps must survive tree validation for failure ordering."""
from datetime import datetime

from infrastructure.persistence.daily_pipeline_collection import _snapshot_rows


def test_snapshot_keeps_test_execution_times():
    started = datetime(2026, 11, 17, 1, 0)
    finished = datetime(2026, 11, 17, 1, 2)
    snapshot = {"run": {"run_id": 100, "workflow": "daily.yaml", "result": "failure"}, "nodes": [
        {"ref": "matrix", "parent_ref": None, "node_type": "matrix", "source_id": None, "name": "run-all-tests (main)", "result": "failure"},
        {"ref": "job", "parent_ref": "matrix", "node_type": "test_job", "source_id": 101, "name": "cpu", "result": "failure", "case_type": "UT", "execution_started_at": started, "failed_at": finished},
    ]}
    _, nodes, _ = _snapshot_rows(100, snapshot)
    assert nodes["job"]["execution_started_at"] == started
    assert nodes["job"]["failed_at"] == finished
