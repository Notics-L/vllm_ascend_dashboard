"""镜像构建监控模型。"""
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from infrastructure.persistence.models import Base


class ImageBuildMonitorTarget(Base):
    """被监控的镜像构建 workflow 及其告警配置。"""

    __tablename__ = "image_build_monitor_targets"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    release_workflow_id = Column(
        BigInteger,
        ForeignKey("release_workflows.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    schedule_cron = Column(String(100), nullable=True)
    timezone = Column(String(64), default="Asia/Shanghai", nullable=False)
    grace_minutes = Column(Integer, default=30, nullable=False)
    lookback_days = Column(Integer, default=2, nullable=False)
    notify_emails = Column(JSON, nullable=True)
    notify_on_failure = Column(Boolean, default=True, nullable=False)
    notify_on_missing = Column(Boolean, default=True, nullable=False)
    notify_on_stuck = Column(Boolean, default=True, nullable=False)
    stuck_threshold_minutes = Column(Integer, default=180, nullable=False)
    cooldown_minutes = Column(Integer, default=60, nullable=False)
    last_checked_at = Column(DateTime, nullable=True)
    last_check_success_at = Column(DateTime, nullable=True)
    last_check_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class ImageBuildAlert(Base):
    """镜像构建异常告警事件。"""

    __tablename__ = "image_build_alerts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    target_id = Column(
        BigInteger,
        ForeignKey("image_build_monitor_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    release_workflow_id = Column(BigInteger, nullable=False, index=True)
    github_run_id = Column(BigInteger, nullable=True, index=True)
    alert_type = Column(String(20), nullable=False, index=True)
    severity = Column(String(20), default="warning", nullable=False)
    status = Column(String(20), default="open", nullable=False, index=True)
    title = Column(String(500), nullable=False)
    message = Column(Text, nullable=True)
    run_url = Column(String(500), nullable=True)
    branch = Column(String(255), nullable=True)
    event = Column(String(50), nullable=True)
    dedupe_key = Column(String(255), nullable=False)
    first_detected_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True)
    last_detected_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String(100), nullable=True)
    notified_at = Column(DateTime, nullable=True)
    last_notified_at = Column(DateTime, nullable=True)
    notification_success = Column(Boolean, nullable=True)
    notification_error = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_image_build_alert_dedupe"),
        Index("ix_image_build_alert_status_type", "status", "alert_type"),
    )
