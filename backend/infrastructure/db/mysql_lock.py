"""Connection-scoped advisory locks shared by processes using the same MySQL database."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from infrastructure.db.base import engine


@asynccontextmanager
async def mysql_named_lock(key: str, *, wait_seconds: int = 0) -> AsyncIterator[bool]:
    params = {
        "name": hashlib.sha256(f"{engine.url.database}:{key}".encode()).hexdigest(),
        "timeout": wait_seconds,
    }
    async with engine.connect() as connection:
        try:
            acquired = await connection.scalar(text("SELECT GET_LOCK(:name, :timeout)"), params)
        except BaseException:
            await connection.invalidate()
            raise
        if acquired == 0:
            yield False
            return
        if acquired != 1:
            await connection.invalidate()
            raise RuntimeError("Could not acquire MySQL advisory lock")
        try:
            yield True
        finally:
            try:
                released = await connection.scalar(text("SELECT RELEASE_LOCK(:name)"), params)
                if released != 1:
                    await connection.invalidate()
            except BaseException:
                await connection.invalidate()
                raise
