from datetime import UTC, datetime
from types import SimpleNamespace

from resource_dashboard.node_utilization import build_node_utilization_heatmap


def _metric(at: datetime, used: float, *, node: str = "node-01"):
    return SimpleNamespace(
        cluster_id=1,
        cluster_name="A3",
        node_name=node,
        collected_at=at,
        npu_used=used,
    )


def test_heatmap_integrates_active_time_per_node_and_bucket():
    start = datetime(2026, 10, 9, 16, tzinfo=UTC)
    end = datetime(2026, 10, 10, 16, tzinfo=UTC)
    result = build_node_utilization_heatmap(
        [
            _metric(start, 8),
            _metric(datetime(2026, 10, 10, 4, tzinfo=UTC), 0),
            _metric(end, 0),
        ],
        start=start,
        end=end,
    )

    bucket = result["nodes"][0]["buckets"][0]
    assert bucket["utilization"] == 50
    assert bucket["active_seconds"] == 12 * 3600
    assert bucket["coverage"] == 1
    assert result["overall_utilization"] == 50


def test_heatmap_distinguishes_missing_data_from_zero_utilization():
    start = datetime(2026, 10, 9, 16, tzinfo=UTC)
    end = datetime(2026, 10, 10, 16, tzinfo=UTC)
    result = build_node_utilization_heatmap(
        [_metric(datetime(2026, 10, 8, tzinfo=UTC), 0)],
        start=start,
        end=end,
    )

    bucket = result["nodes"][0]["buckets"][0]
    assert bucket["utilization"] is None
    assert bucket["coverage"] == 0


def test_heatmap_keeps_same_node_name_separate_across_pools():
    start = datetime(2026, 10, 9, 16, tzinfo=UTC)
    end = datetime(2026, 10, 10, 16, tzinfo=UTC)
    rows = [
        _metric(start, 1),
        SimpleNamespace(
            cluster_id=2,
            cluster_name="A5",
            node_name="node-01",
            collected_at=start,
            npu_used=1,
        ),
    ]

    result = build_node_utilization_heatmap(rows, start=start, end=end)

    assert [(node["cluster_name"], node["node_name"]) for node in result["nodes"]] == [
        ("A3", "node-01"),
        ("A5", "node-01"),
    ]
