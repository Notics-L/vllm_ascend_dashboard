from datetime import UTC, datetime
from types import SimpleNamespace

from npu_queue.service import _queue_delay_dates, _queue_delay_window
from npu_queue.snapshots import current_counts


def _fact(*, status: str, cards: int, started_at=None, completed_at=None):
    return SimpleNamespace(
        mapping_status="matched",
        mapped_cards=cards,
        status=status,
        queued_at=datetime(2026, 10, 10, 6, 0, tzinfo=UTC),
        started_at=started_at,
        completed_at=completed_at,
    )


def test_current_counts_separates_waiting_and_running_cards():
    facts = [
        _fact(status="queued", cards=8),
        _fact(status="in_progress", cards=4, started_at=datetime(2026, 10, 10, 6, 2, tzinfo=UTC)),
        _fact(status="completed", cards=16, started_at=datetime(2026, 10, 10, 5, 0, tzinfo=UTC), completed_at=datetime(2026, 10, 10, 6, 0, tzinfo=UTC)),
    ]

    assert current_counts(facts) == {
        "waitingJobs": 1,
        "waitingCards": 8,
        "runningJobs": 1,
        "runningCards": 4,
    }


def test_queue_delay_window_uses_seven_beijing_calendar_dates():
    start, end = _queue_delay_window(datetime(2026, 10, 10, 0, 30, tzinfo=UTC))

    assert (start, end) == ("2026-10-04", "2026-10-10")
    assert _queue_delay_dates(datetime(2026, 10, 10, 0, 30, tzinfo=UTC)) == [
        "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07",
        "2026-10-08", "2026-10-09", "2026-10-10",
    ]
