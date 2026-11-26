"""Definitions for the image build form, synchronized from GitHub."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, String
from sqlalchemy.dialects import mysql

from infrastructure.persistence.models import Base


class ImageBuildWorkflowDefinition(Base):
    __tablename__ = "image_build_workflow_definitions"

    repository = Column(String(140), primary_key=True)
    branch_name = Column(
        String(255).with_variant(mysql.VARCHAR(255, collation="utf8mb4_bin"), "mysql"),
        primary_key=True,
    )
    inputs_json = Column(JSON, nullable=False)
    inputs_hash = Column(String(64), nullable=False)
    is_available = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    update_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
