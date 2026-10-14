import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


def _fk(table: str, **kw):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id"), **kw)


class RoomType(Base, IdMixin, TimestampMixin):
    __tablename__ = "room_types"
    __table_args__ = (UniqueConstraint("hotel_id", "code"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    max_occupancy: Mapped[int] = mapped_column(Integer, nullable=False)
    base_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class Room(Base, IdMixin, TimestampMixin):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("hotel_id", "number"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    room_type_id: Mapped[uuid.UUID] = _fk("room_types", index=True, nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    floor: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class RoomInventory(Base, IdMixin, TimestampMixin):
    """Per room-type, per-night capacity; booked count is enforced transactionally."""

    __tablename__ = "room_inventory"
    __table_args__ = (
        UniqueConstraint("room_type_id", "night"),
        CheckConstraint("booked >= 0 AND booked <= total", name="booked_within_total"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    room_type_id: Mapped[uuid.UUID] = _fk("room_types", index=True, nullable=False)
    night: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    booked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class ReservationStatus(str, enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    checked_in = "checked_in"
    checked_out = "checked_out"


class Reservation(Base, IdMixin, TimestampMixin):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("check_out > check_in", name="valid_dates"),
        UniqueConstraint("organization_id", "idempotency_key"),
        Index("ix_res_hotel_checkin", "hotel_id", "check_in"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    guest_id: Mapped[uuid.UUID] = _fk("guests", index=True, nullable=False)
    room_type_id: Mapped[uuid.UUID] = _fk("room_types", nullable=False)
    room_id: Mapped[uuid.UUID | None] = _fk("rooms")
    confirmation_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus, name="reservation_status"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(100))


class TicketStatus(str, enum.Enum):
    open = "open"
    assigned = "assigned"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"
    escalated = "escalated"


class TicketPriority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class ServiceRequest(Base, IdMixin, TimestampMixin):
    __tablename__ = "service_requests"
    __table_args__ = (Index("ix_sr_hotel_status", "hotel_id", "status"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    guest_id: Mapped[uuid.UUID | None] = _fk("guests", index=True)
    reservation_id: Mapped[uuid.UUID | None] = _fk("reservations")
    department_id: Mapped[uuid.UUID | None] = _fk("departments", index=True)
    assigned_to: Mapped[uuid.UUID | None] = _fk("users")
    ticket_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    room_number: Mapped[str | None] = mapped_column(String(20))
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority"), default=TicketPriority.normal, nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status"), default=TicketStatus.open, nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    internal_notes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


class ApprovalRequest(Base, IdMixin, TimestampMixin):
    __tablename__ = "approval_requests"
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)  # late_checkout, ...
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    requested_by_guest_id: Mapped[uuid.UUID | None] = _fk("guests")
    decided_by: Mapped[uuid.UUID | None] = _fk("users")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GuestFeedback(Base, IdMixin, TimestampMixin):
    __tablename__ = "guest_feedback"
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    guest_id: Mapped[uuid.UUID | None] = _fk("guests")
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),)
