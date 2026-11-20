"""Explicit enable overrides existing faults, not future failures."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daily_pipeline.cases import DailyCaseService
from infrastructure.persistence.daily_pipeline_tracking import is_case_fault_blocked


@pytest.mark.parametrize("fault_offset,enabled_offset,blocked", [
    (None, None, False), (0, None, True), (0, 1, False), (0, 0, False), (1, 0, True),
])
def test_fault_override_cutoff(fault_offset, enabled_offset, blocked):
    point = datetime(2026, 11, 17)
    row = SimpleNamespace(name="case", hardware="A3-560T", manual_enabled_at=None if enabled_offset is None else point + timedelta(seconds=enabled_offset))
    faults = {} if fault_offset is None else {("case", "a3-560t"): point + timedelta(seconds=fault_offset)}
    assert is_case_fault_blocked(row, faults) is blocked


def catalog_case():
    return SimpleNamespace(
        name="case",
        hardware="a3-560t",
        enabled=True,
        auto_disabled=True,
        manual_enabled_at=None,
        tags=["old"],
        test_type="single_node",
        source_branch="main",
        is_daily=True,
        success_count=0,
        failure_count=0,
    )


def repository(row):
    repo = SimpleNamespace(lock_daily_cases=AsyncMock(return_value=[row]), commit=AsyncMock())
    async def reconcile(rows):
        for case in rows:
            case.auto_disabled = is_case_fault_blocked(case, {("case", "a3-560t"): datetime(2026, 1, 1)})
    repo.reconcile_availability = AsyncMock(side_effect=reconcile)
    return repo


@pytest.mark.asyncio
async def test_single_enable_releases_fault_without_changing_processing():
    row = catalog_case()
    repo = repository(row)
    result = await DailyCaseService(repo).edit_case("a3-560t:case", tags=None, enabled=True)
    assert result["status"] == "启用"
    assert row.manual_enabled_at is not None
    assert not row.auto_disabled
    repo.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_enable_releases_fault_even_if_manual_flag_was_already_on():
    row = catalog_case()
    result = await DailyCaseService(repository(row)).set_enabled({"a3-560t:case"}, True)
    assert result == {"updated": 1}
    assert row.enabled and not row.auto_disabled
    assert row.manual_enabled_at is not None


@pytest.mark.asyncio
async def test_tag_only_edit_does_not_override_fault():
    row = catalog_case()
    repo = repository(row)
    await DailyCaseService(repo).edit_case("a3-560t:case", tags=["new"], enabled=None)
    assert row.tags == ["new"]
    assert row.auto_disabled and row.manual_enabled_at is None
    repo.reconcile_availability.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_disable_does_not_recover_when_fault_clears():
    row = catalog_case()
    await DailyCaseService(repository(row)).set_enabled({"a3-560t:case"}, False)
    row.auto_disabled = False
    assert not row.enabled
    assert row.manual_enabled_at is None
