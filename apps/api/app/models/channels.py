import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


def _fk(table: str, **kw):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id"), **kw)


class ChannelEvent(Base, IdMixin, TimestampMixin):
    """One row per inbound provider delivery. The unique key is what makes webhooks idempotent."""

    __tablename__ = "channel_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id"),)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = _fk("organizations", index=True)
    hotel_id: Mapped[uuid.UUID | None] = _fk("hotels", index=True)
    conversation_id: Mapped[uuid.UUID | None] = _fk("conversations")
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class OutboundMessage(Base, IdMixin, TimestampMixin):
    """A message to deliver through a channel, with delivery state and retry schedule."""

    __tablename__ = "outbound_messages"
    __table_args__ = (
        UniqueConstraint("message_id", "part"),
        Index("ix_outbound_due", "status", "next_attempt_at"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = _fk("conversations", index=True, nullable=False)
    message_id: Mapped[uuid.UUID] = _fk("messages", nullable=False)
    part: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)


class Notification(Base, IdMixin, TimestampMixin):
    """Staff-facing queue entry (the dashboard bell). Contains no guest message content."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_hotel_created", "hotel_id", "created_at"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), default="info", nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    department_id: Mapped[uuid.UUID | None] = _fk("departments")
    roles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


class NotificationRead(Base, IdMixin):
    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("notification_id", "user_id"),)
    notification_id: Mapped[uuid.UUID] = _fk("notifications", index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = _fk("users", index=True, nullable=False)
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
