"""Saved manual or scheduled tasks for dispatching the image build workflow."""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.models import Base

TriggerInputValue = str | bool | int | float | None
SourceRefMode = Literal["branch", "recommended", "manual"]


class ImageBuildTriggerTask(Base):
    __tablename__ = "image_build_trigger_tasks"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_branch: Mapped[str] = mapped_column(String(255))
    cron_expression: Mapped[str] = mapped_column(String(120))
    source_ref_mode: Mapped[SourceRefMode] = mapped_column(String(16), server_default="branch")
    inputs_json: Mapped[dict[str, TriggerInputValue]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    update_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
