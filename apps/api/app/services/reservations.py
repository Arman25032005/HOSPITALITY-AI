import secrets
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Guest, Reservation, ReservationStatus, RoomInventory, RoomType,
)
from app.services import audit
from app.services.errors import ConflictError, NotFoundError, ValidationFailed

MAX_STAY_NIGHTS = 30
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True)
class RoomOption:
    room_type_id: uuid.UUID
    code: str
    name: str
    max_occupancy: int
    available: int
    nightly_rates: list[Decimal]

    @property
    def total_price(self) -> Decimal:
        return sum(self.nightly_rates, Decimal("0"))


def _nights(check_in: date, check_out: date) -> list[date]:
    if check_out <= check_in:
        raise ValidationFailed("check_out must be after check_in")
    n = (check_out - check_in).days
    if n > MAX_STAY_NIGHTS:
        raise ValidationFailed(f"Stays are limited to {MAX_STAY_NIGHTS} nights")
    if check_in < date.today():
        raise ValidationFailed("check_in cannot be in the past")
    return [check_in + timedelta(days=i) for i in range(n)]


def _new_code() -> str:
    return "R-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def search_availability(
    db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID, check_in: date, check_out: date,
    adults: int,
) -> list[RoomOption]:
    if adults < 1:
        raise ValidationFailed("adults must be at least 1")
    nights = _nights(check_in, check_out)
    options: list[RoomOption] = []
    types = db.scalars(
        select(RoomType).where(
            RoomType.hotel_id == hotel_id, RoomType.organization_id == org_id,
            RoomType.max_occupancy >= adults,
        ).order_by(RoomType.base_rate)
    )
    for rt in types:
        rows = db.scalars(
            select(RoomInventory).where(
                RoomInventory.room_type_id == rt.id, RoomInventory.night.in_(nights)
            ).order_by(RoomInventory.night)
        ).all()
        if len(rows) != len(nights):
            continue  # inventory not published for every night
        available = min(r.total - r.booked for r in rows)
        if available > 0:
            options.append(RoomOption(rt.id, rt.code, rt.name, rt.max_occupancy, available,
                                      [r.rate for r in rows]))
    return options


def create_reservation(
    db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID, guest_id: uuid.UUID,
    room_type_id: uuid.UUID, check_in: date, check_out: date, adults: int,
    idempotency_key: str | None = None, actor_type: str = "user", actor_id: str | None = None,
) -> Reservation:
    """Create a confirmed reservation atomically.

    Inventory rows are locked (FOR UPDATE, ordered by night) and availability is re-checked
    under the lock, so concurrent bookings of the last room cannot both succeed.
    """
    nights = _nights(check_in, check_out)
    if adults < 1:
        raise ValidationFailed("adults must be at least 1")

    if idempotency_key:
        existing = db.scalar(select(Reservation).where(
            Reservation.organization_id == org_id,
            Reservation.idempotency_key == idempotency_key))
        if existing:
            return _same_request_or_conflict(existing, hotel_id, guest_id, room_type_id,
                                             check_in, check_out, adults)

    guest = db.get(Guest, guest_id)
    if guest is None or guest.organization_id != org_id:
        raise NotFoundError("Guest not found")
    rt = db.get(RoomType, room_type_id)
    if rt is None or rt.hotel_id != hotel_id or rt.organization_id != org_id:
        raise NotFoundError("Room type not found")
    if adults > rt.max_occupancy:
        raise ValidationFailed(f"Room type sleeps at most {rt.max_occupancy}")

    rows = db.scalars(
        select(RoomInventory).where(
            RoomInventory.room_type_id == rt.id, RoomInventory.night.in_(nights)
        ).order_by(RoomInventory.night).with_for_update()
    ).all()
    if len(rows) != len(nights):
        raise ConflictError("Room type is not available for the requested dates")
    if any(r.booked >= r.total for r in rows):
        raise ConflictError("Room type is sold out for part of the requested stay")

    total = Decimal("0")
    for r in rows:
        r.booked += 1
        total += r.rate

    res = Reservation(
        organization_id=org_id, hotel_id=hotel_id, guest_id=guest_id, room_type_id=rt.id,
        confirmation_code=_new_code(), check_in=check_in, check_out=check_out, adults=adults,
        total_price=total, status=ReservationStatus.confirmed, idempotency_key=idempotency_key,
    )
    db.add(res)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(select(Reservation).where(
                Reservation.organization_id == org_id,
                Reservation.idempotency_key == idempotency_key))
            if existing:
                return _same_request_or_conflict(existing, hotel_id, guest_id, room_type_id,
                                                 check_in, check_out, adults)
        raise
    audit.record(db, org_id=org_id, hotel_id=hotel_id, action="reservation.created",
                 actor_type=actor_type, actor_id=actor_id, entity_type="reservation",
                 entity_id=str(res.id), details={"code": res.confirmation_code,
                                                 "total": str(total)})
    db.commit()
    return res


def _same_request_or_conflict(existing: Reservation, hotel_id, guest_id, room_type_id,
                              check_in, check_out, adults) -> Reservation:
    same = (existing.hotel_id, existing.guest_id, existing.room_type_id, existing.check_in,
            existing.check_out, existing.adults) == (hotel_id, guest_id, room_type_id,
                                                     check_in, check_out, adults)
    if not same:
        raise ConflictError("Idempotency key was already used with different parameters")
    return existing


def get_reservation(db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID,
                    reservation_id: uuid.UUID) -> Reservation:
    res = db.get(Reservation, reservation_id)
    if res is None or res.organization_id != org_id or res.hotel_id != hotel_id:
        raise NotFoundError("Reservation not found")
    return res


def cancel_reservation(
    db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID, reservation_id: uuid.UUID,
    actor_type: str = "user", actor_id: str | None = None,
) -> Reservation:
    res = db.scalar(select(Reservation).where(Reservation.id == reservation_id)
                    .with_for_update())
    if res is None or res.organization_id != org_id or res.hotel_id != hotel_id:
        raise NotFoundError("Reservation not found")
    if res.status == ReservationStatus.cancelled:
        return res
    if res.status != ReservationStatus.confirmed:
        raise ConflictError("Only confirmed reservations can be cancelled")
    nights = [res.check_in + timedelta(days=i) for i in range((res.check_out - res.check_in).days)]
    rows = db.scalars(
        select(RoomInventory).where(
            RoomInventory.room_type_id == res.room_type_id, RoomInventory.night.in_(nights)
        ).order_by(RoomInventory.night).with_for_update()
    ).all()
    for r in rows:
        r.booked = max(0, r.booked - 1)
    res.status = ReservationStatus.cancelled
    audit.record(db, org_id=org_id, hotel_id=hotel_id, action="reservation.cancelled",
                 actor_type=actor_type, actor_id=actor_id, entity_type="reservation",
                 entity_id=str(res.id), details={"code": res.confirmation_code})
    db.commit()
    return res
