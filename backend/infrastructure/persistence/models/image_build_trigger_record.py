"""Submission history for saved image build tasks."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.models import Base


class ImageBuildTriggerRecord(Base):
    __tablename__ = "image_build_trigger_records"
    __table_args__ = (
        Index("ix_image_build_trigger_record_active", "task_id", "status", "triggered_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int]
    repository: Mapped[str] = mapped_column(String(140))
    workflow_branch: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(255))
    run_id: Mapped[int | None] = mapped_column(BigInteger)
    dispatch_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    github_status_code: Mapped[int | None]
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
