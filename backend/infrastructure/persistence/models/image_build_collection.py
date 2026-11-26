"""Durable collection state for dashboard-triggered image builds."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.persistence.models import Base


class ImageBuildCollectionRun(Base):
    __tablename__ = "image_build_collection_runs"
    __table_args__ = (
        Index("ix_image_build_collection_due", "next_collect_at", "run_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    trigger_record_id: Mapped[int] = mapped_column(
        ForeignKey("image_build_trigger_records.id"), nullable=False, unique=True,
    )
    workflow_name: Mapped[str | None] = mapped_column(String(255))
    workflow_sha: Mapped[str | None] = mapped_column(String(40))
    github_status: Mapped[str] = mapped_column(String(24), default="queued")
    conclusion: Mapped[str | None] = mapped_column(String(24))
    run_attempt: Mapped[int] = mapped_column(default=0)
    pending_attempt: Mapped[int | None]
    rerun_acknowledged: Mapped[bool] = mapped_column(default=False)
    rerun_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_collect_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(255))
    revision: Mapped[int] = mapped_column(default=0)
    matrix_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    publication_status: Mapped[str] = mapped_column(String(16), default="pending")
    publication_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
