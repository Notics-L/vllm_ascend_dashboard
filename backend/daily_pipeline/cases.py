"""Daily case catalog and selection rules."""
import logging
import time
from datetime import UTC, datetime

import httpx
import yaml
from pydantic import BaseModel, Field, TypeAdapter

from infrastructure.clients.daily_pipeline_config import read_nightly_config
from infrastructure.persistence.daily_pipeline_cases import DailyCaseRepository

logger = logging.getLogger(__name__)

# Hardware paths dispatched by schedule_daily_test.yaml.
HARDWARE = ("a2", "a3", "a3-560t", "a5")
TEST_TYPES = {
    "single_node": "单节点", "multi_node": "多节点", "double_node": "双节点",
    "multi_card": "多卡", "accuracy": "精度",
}


class ConfigCase(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CatalogSourceError(Exception):
    pass


def parse_catalog(content: str) -> dict[tuple[str, str], str]:
    config = yaml.safe_load(content)
    if not isinstance(config, dict) or not any(hw in config for hw in HARDWARE):
        raise ValueError("Missing Daily hardware sections")
    case_list = TypeAdapter(list[ConfigCase])
    definitions = {}
    for hardware in HARDWARE:
        sections = config.get(hardware, {})
        if not isinstance(sections, dict):
            raise ValueError(f"Invalid hardware section: {hardware}")
        for section, groups in sections.items():
            if section not in TEST_TYPES:
                continue
            buckets = ("nightly", "pr_only") if section == "accuracy" else ("test_config",)
            if not isinstance(groups, dict) or not any(key in groups for key in buckets):
                raise ValueError(f"Invalid Nightly section: {hardware}/{section}")
            for bucket in buckets:
                for case in case_list.validate_python(groups.get(bucket, [])):
                    definitions[(case.name, hardware)] = section
    return definitions


def case_key(row):
    return f"{row.hardware}:{row.name}"


def case_view(row):
    key = case_key(row)
    return {
        "id": key, "name": row.name, "hardware": row.hardware.upper(),
        "testType": TEST_TYPES[row.test_type], "tags": row.tags,
        "status": "启用" if row.enabled and not row.auto_disabled else "禁用", "branch": row.source_branch,
        "manualEnabled": row.enabled, "autoDisabled": row.auto_disabled,
        "successCount": row.success_count, "failureCount": row.failure_count,
        "dailyCaseId": key if row.is_daily else None, "nightlyCaseId": key, "executionTimes": [],
    }


class DailyCaseService:
    def __init__(self, repository: DailyCaseRepository):
        self.repository = repository

    async def list_cases(self, *, is_daily: bool):
        rows = await self.repository.list_cases(is_daily=is_daily)
        if not is_daily and not rows and not await self.repository.has_cases():
            await self.refresh_catalog(only_if_empty=True)
            rows = await self.repository.list_cases(is_daily=is_daily)
        failures = await self.repository.active_failure_ids()
        return [{**case_view(row), "processingFailureId": failures.get((row.name, row.hardware.lower()))} for row in rows]

    async def refresh_catalog(self, *, only_if_empty: bool = False):
        started_at = time.perf_counter()
        logger.info("Starting Daily catalog sync (only_if_empty=%s)", only_if_empty)
        try:
            async with self.repository.catalog_refresh_lock():
                # A concurrent request or the scheduler may have initialized the catalog.
                if only_if_empty and await self.repository.has_cases(for_update=True):
                    logger.info(
                        "Skipped Daily catalog sync: cases already exist (%.3fs)",
                        time.perf_counter() - started_at,
                    )
                    return
                try:
                    source = await self.repository.catalog_source()
                    branch = source.get("source_branch")
                    repository = source.get("repository")
                    path = source.get("nightly_config_path")
                    if not (branch and repository and path):
                        raise ValueError("Missing Daily pipeline catalog settings")
                    logger.info(
                        "Reading Daily catalog source: repository=%s branch=%s path=%s",
                        repository, branch, path,
                    )
                    definitions = parse_catalog(await read_nightly_config(repository, branch, path))
                except (httpx.HTTPError, yaml.YAMLError, ValueError) as exc:
                    raise CatalogSourceError from exc
                logger.info("Parsed %d Daily catalog cases", len(definitions))
                await self.repository.sync_catalog(branch, definitions)
            logger.info(
                "Daily catalog sync committed: %d cases in %.3fs",
                len(definitions), time.perf_counter() - started_at,
            )
        except Exception:
            logger.exception("Daily catalog sync failed after %.3fs", time.perf_counter() - started_at)
            raise

    async def save_selection(self, keys: set[str]):
        rows = await self.repository.list_cases(for_update=True)
        if keys.difference(case_key(row) for row in rows):
            raise ValueError("部分 Nightly 用例已变更，请刷新后重新选择")
        added = removed = 0
        for row in rows:
            selected = case_key(row) in keys
            if selected != row.is_daily:
                row.is_daily = selected
                if selected:
                    added += 1
                else:
                    removed += 1
        await self.repository.commit()
        return {"added": added, "removed": removed}

    async def edit_case(self, key: str, *, tags: list[str] | None, enabled: bool | None):
        rows = await self.repository.lock_daily_cases({key})
        if not rows:
            raise LookupError("用例不存在或已移出 Daily 清单")
        row = rows[0]
        if tags is not None:
            row.tags = list(dict.fromkeys(tags))
        if enabled is not None:
            row.enabled = enabled
            row.manual_enabled_at = datetime.now(UTC).replace(tzinfo=None) if enabled else None
            await self.repository.reconcile_availability(rows)
        await self.repository.commit()
        return case_view(row)

    async def set_enabled(self, keys: set[str], enabled: bool):
        rows = await self.repository.lock_daily_cases(keys)
        if len(rows) != len(keys):
            raise LookupError("部分用例已移出 Daily 清单，请刷新后重试")
        changed = 0
        point = datetime.now(UTC).replace(tzinfo=None)
        for row in rows:
            if (row.enabled and not row.auto_disabled) != enabled:
                changed += 1
            row.enabled = enabled
            row.manual_enabled_at = point if enabled else None
        await self.repository.reconcile_availability(rows)
        await self.repository.commit()
        return {"updated": changed}

    async def remove_cases(self, keys: set[str]):
        rows = await self.repository.lock_daily_cases(keys)
        for row in rows:
            row.is_daily = False
        await self.repository.commit()
        return {"deleted": len(rows)}
