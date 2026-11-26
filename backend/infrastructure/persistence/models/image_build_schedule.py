"""Settings for recurring image build tasks."""

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, text

from infrastructure.persistence.models import Base

WORKFLOW_SYNC_TASK = "image_build_workflow_sync"


class ImageBuildScheduledTask(Base):
    __tablename__ = "image_build_scheduled_tasks"
    __table_args__ = (
        CheckConstraint("interval_minutes > 0", name="ck_image_build_task_interval"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    task_key = Column(String(64), primary_key=True)
    enabled = Column(Boolean, nullable=False, server_default="1")
    interval_minutes = Column(Integer, nullable=False)
    update_at = Column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )
