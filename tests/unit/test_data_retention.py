from datetime import date

from infrastructure.storage.data_retention import (
    remove_all_but_latest_date,
    remove_older_than_days,
)


def _date_dir(root, name: str):
    path = root / name
    path.mkdir()
    (path / "artifact.txt").write_text(name, encoding="utf-8")
    return path


def test_coverage_retention_keeps_only_latest_date(tmp_path):
    oldest = _date_dir(tmp_path, "2026-10-20")
    middle = _date_dir(tmp_path, "2026-10-21")
    latest = _date_dir(tmp_path, "2026-10-22")
    _date_dir(tmp_path, "misc")

    removed = remove_all_but_latest_date(tmp_path)

    assert removed == [oldest, middle]
    assert latest.exists()
    assert (tmp_path / "misc").exists()


def test_claude_log_retention_keeps_last_seven_calendar_dates(tmp_path):
    expired = _date_dir(tmp_path, "2026-10-15")
    boundary = _date_dir(tmp_path, "2026-10-16")
    current = _date_dir(tmp_path, "2026-10-22")

    removed = remove_older_than_days(tmp_path, retention_days=7, today=date(2026, 10, 22))

    assert removed == [expired]
    assert boundary.exists()
    assert current.exists()
