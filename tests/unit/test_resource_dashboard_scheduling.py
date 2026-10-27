from types import SimpleNamespace

from resource_dashboard.service import ResourceDashboardService


def test_scheduling_condition_returns_unschedulable_reason_and_message():
    status = SimpleNamespace(conditions=[
        SimpleNamespace(type="Initialized", status="True", reason=None, message=None),
        SimpleNamespace(
            type="PodScheduled", status="False", reason="Unschedulable",
            message="0/2 nodes are available: Insufficient huawei.com/Ascend910.",
        ),
    ])

    assert ResourceDashboardService._scheduling_condition(status) == (
        "Unschedulable", "0/2 nodes are available: Insufficient huawei.com/Ascend910.",
    )


def test_scheduling_condition_ignores_successful_or_missing_conditions():
    assert ResourceDashboardService._scheduling_condition(SimpleNamespace(conditions=[])) == (None, None)
    assert ResourceDashboardService._scheduling_condition(SimpleNamespace(conditions=[
        SimpleNamespace(type="PodScheduled", status="True", reason="Scheduled", message="ok"),
    ])) == (None, None)
