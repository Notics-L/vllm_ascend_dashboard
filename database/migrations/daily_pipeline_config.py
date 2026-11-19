"""Create and seed the Daily pipeline settings without replacing saved values."""
from infrastructure.core.config import settings
from infrastructure.db.base import engine
from infrastructure.persistence.daily_pipeline_settings import (
    DEFAULT_REVERT_DEADLINE_MINUTES,
    DEFAULT_VERSION_BUILD_WORKFLOW,
    REVERT_DEADLINE_KEY,
)
from infrastructure.persistence.models import DailyPipelineConfig
from sqlalchemy.dialects.mysql import insert
from tooling.parsers.nightly_config_parser import CONFIG_PATH


async def run() -> None:
    defaults = {
        "repository": f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}",
        "source_branch": "main",
        "nightly_config_path": CONFIG_PATH,
        REVERT_DEADLINE_KEY: str(DEFAULT_REVERT_DEADLINE_MINUTES),
        "version_image_build_workflow": DEFAULT_VERSION_BUILD_WORKFLOW,
        "version_image_build_repository": None,
        "github_token": None,
    }
    async with engine.begin() as conn:
        await conn.run_sync(DailyPipelineConfig.__table__.create, checkfirst=True)
        stmt = insert(DailyPipelineConfig).values([
            {"config_key": key, "config_value": value} for key, value in defaults.items()
        ])
        await conn.execute(stmt.on_duplicate_key_update(config_key=stmt.inserted.config_key))
