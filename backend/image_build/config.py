"""Read the repository configured for image builds."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import ImageBuildConfig

REPOSITORY_KEY = "repository"
_REPOSITORY_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,38}/(?!\.{1,2}$)[a-z0-9_.-]{1,100}")


class ImageBuildRepositoryError(ValueError):
    pass


async def get_image_build_repository(db: AsyncSession) -> str:
    value = await db.scalar(
        select(ImageBuildConfig.config_value).where(ImageBuildConfig.config_key == REPOSITORY_KEY)
    )
    repository = value.strip().lower() if value else ""
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ImageBuildRepositoryError("请在 image_build_config 中设置 repository，格式为 owner/repo")
    return repository
