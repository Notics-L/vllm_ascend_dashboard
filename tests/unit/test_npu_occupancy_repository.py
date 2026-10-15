from datetime import UTC, datetime, timedelta

from resource_dashboard.npu_occupancy_repository import is_stale_sync


def test_sync_lease_identifies_expired_running_marker() -> None:
    now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)

    assert is_stale_sync(now - timedelta(minutes=31), now=now, lease_seconds=1800)
    assert not is_stale_sync(now - timedelta(minutes=29), now=now, lease_seconds=1800)


def test_sync_lease_accepts_mysql_naive_timestamp() -> None:
    now = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)

    assert is_stale_sync(datetime(2026, 10, 15, 7, 0), now=now, lease_seconds=1800)
    assert is_stale_sync(None, now=now, lease_seconds=1800)
