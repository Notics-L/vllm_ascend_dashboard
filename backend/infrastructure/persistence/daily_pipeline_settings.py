from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import DailyPipelineConfig as Config

REVERT_DEADLINE_KEY = "revert_deadline_minutes"
DEFAULT_REVERT_DEADLINE_MINUTES = 30
DEFAULT_VERSION_BUILD_WORKFLOW = "schedule_nightly_image_build_with_base.yaml"


def revert_deadline_minutes(config: dict[str, str | None]) -> int:
    value = config.get(REVERT_DEADLINE_KEY)
    minutes = DEFAULT_REVERT_DEADLINE_MINUTES if value is None else int(value)
    if minutes < 0:
        raise ValueError("回退 PR 截止提前量必须是非负整数")
    return minutes


async def read_settings(db: AsyncSession, *keys: str) -> dict[str, str | None]:
    result = await db.execute(
        select(Config.config_key, Config.config_value).where(Config.config_key.in_(keys))
    )
    return dict(result.tuples().all())


async def has_setting(db: AsyncSession, key: str) -> bool:
    query = select(Config.id).where(Config.config_key == key, Config.config_value != "").exists()
    return bool(await db.scalar(select(query)))


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    stmt = insert(Config).values(config_key=key, config_value=value)
    await db.execute(stmt.on_duplicate_key_update(config_value=stmt.inserted.config_value))
