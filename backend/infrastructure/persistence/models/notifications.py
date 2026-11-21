"""通知服务相关模型：联系人、联系人组、通知事件与投递记录。"""
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


class NotificationContact(Base):
    """通知联系人。"""

    __tablename__ = "notification_contacts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(32), nullable=True, index=True)
    phone_verified_at = Column(DateTime, nullable=True)
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class NotificationGroup(Base):
    """通知联系人组。"""

    __tablename__ = "notification_groups"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(500), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class NotificationGroupMember(Base):
    """联系人组成员关系。"""

    __tablename__ = "notification_group_members"

    group_id = Column(
        BigInteger,
        ForeignKey("notification_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    contact_id = Column(
        BigInteger,
        ForeignKey("notification_contacts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class NotificationEvent(Base):
    """一次通知事件，可对应多个渠道投递。"""

    __tablename__ = "notification_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, default="notification")
    severity = Column(String(20), nullable=False, default="info", index=True)
    title = Column(String(500), nullable=False)
    summary = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    dedupe_key = Column(String(255), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True)

    __table_args__ = (
        Index("ix_notification_events_source_created", "source", "created_at"),
    )


class NotificationDelivery(Base):
    """通知渠道投递记录。"""

    __tablename__ = "notification_deliveries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_id = Column(
        BigInteger,
        ForeignKey("notification_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = Column(String(20), nullable=False, default="email", index=True)
    recipient = Column(String(255), nullable=False)
    contact_id = Column(
        BigInteger,
        ForeignKey("notification_contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="pending", index=True)
    provider = Column(String(50), nullable=True)
    provider_message_id = Column(String(255), nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True)

    __table_args__ = (
        Index("ix_notification_deliveries_event_channel", "event_id", "channel"),
    )


class NotificationAction(Base):
    """语音 IVR 按键动作与审计记录。"""

    __tablename__ = "notification_actions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    delivery_id = Column(
        BigInteger,
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_call_id = Column(String(255), nullable=False, index=True)
    digit = Column(String(10), nullable=False)
    action = Column(String(20), nullable=False)
    stage = Column(String(20), nullable=False, default="execute")
    status = Column(String(20), nullable=False, default="pending", index=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    executed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "provider_call_id", "digit", "stage", name="uq_notification_actions_callback"
        ),
    )
