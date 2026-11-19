"""Create the four-column recipient mapping table without altering existing data."""
from infrastructure.db.base import engine
from infrastructure.persistence.models import DailyPipelineNotificationRecipient


async def run():
    async with engine.begin() as conn:
        await conn.run_sync(DailyPipelineNotificationRecipient.__table__.create, checkfirst=True)
