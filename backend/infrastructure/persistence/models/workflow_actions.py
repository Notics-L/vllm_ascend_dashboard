"""Workflow Actions 用户授权与操作日志。"""

from datetime import UTC, datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from infrastructure.persistence.models import Base


class WorkflowActionPermission(Base):
    __tablename__ = "workflow_action_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_path = Column(String(500, collation="utf8mb4_bin"), nullable=False)
    granted_by = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "workflow_path", name="uq_workflow_action_user_path"),
    )


class WorkflowActionLog(Base):
    __tablename__ = "workflow_action_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, index=True)
    action = Column(String(30), nullable=False)
    workflow_path = Column(String(500), nullable=False)
    command_text = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=False, index=True)


class WorkflowSchedulePermission(Base):
    __tablename__ = "workflow_schedule_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    granted_by = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=False)


class WorkflowActionSchedule(Base):
    __tablename__ = "workflow_action_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    workflow_path = Column(String(500), nullable=False)
    ref = Column(String(200), nullable=False, default="main")
    inputs_yaml = Column(Text, nullable=False, default="")
    cron_expression = Column(String(100), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    setter_user_id = Column(Integer, nullable=False, index=True)
    setter_username = Column(String(50), nullable=False)
    created_at = Column(TIMESTAMP, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(
        TIMESTAMP,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_run_at = Column(TIMESTAMP, nullable=True)
    running_at = Column(TIMESTAMP, nullable=True)
    last_output = Column(Text, nullable=True)
    last_submission_status = Column(String(16), nullable=True)
