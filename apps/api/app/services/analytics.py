import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent, Department, GuestFeedback, Reservation, ReservationStatus, Role, ServiceRequest,
    TicketStatus, User,
)
from app.services.errors import ForbiddenError, ValidationFailed

OPEN = (TicketStatus.open, TicketStatus.assigned, TicketStatus.in_progress, TicketStatus.escalated)
THEMES = {  # transparent keyword themes for recurring-complaint counts
    "noise": ("noise", "noisy", "loud"),
    "cleanliness": ("dirty", "filthy", "unclean", "smell", "stain", "bugs"),
    "billing": ("charge", "charged", "refund", "bill", "overcharged"),
    "staff": ("rude", "unhelpful", "staff", "manager"),
    "safety": ("unsafe", "stolen", "theft", "harass", "injur"),
}


def summary(db: Session, *, user: User, hotel_id: uuid.UUID, days: int = 7) -> dict:
    """Deterministic metrics straight from records. Time window: the last `days` days for
    created/resolved/escalated counts; open and overdue are point-in-time."""
    if user.role not in (Role.admin, Role.manager):
        raise ForbiddenError("Analytics are limited to managers and administrators")
    if not 1 <= days <= 90:
        raise ValidationFailed("days must be between 1 and 90")
    org = user.organization_id
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    t = ServiceRequest
    base = (t.organization_id == org, t.hotel_id == hotel_id)

    by_dept = dict(db.execute(
        select(Department.name, func.count(t.id)).join(t, t.department_id == Department.id)
        .where(*base, t.status.in_(OPEN)).group_by(Department.name)).all())
    by_priority = {p.value: n for p, n in db.execute(
        select(t.priority, func.count(t.id)).where(*base, t.status.in_(OPEN))
        .group_by(t.priority)).all()}
    overdue = db.scalar(select(func.count(t.id)).where(
        *base, t.status.in_(OPEN), t.due_at.is_not(None), t.due_at < now)) or 0
    avg_secs = db.scalar(select(func.avg(func.extract("epoch", t.resolved_at - t.created_at)))
                         .where(*base, t.resolved_at.is_not(None), t.resolved_at >= since))
    escalations = db.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.organization_id == org, AuditEvent.hotel_id == hotel_id,
        AuditEvent.action == "ticket.escalated", AuditEvent.created_at >= since)) or 0
    created = db.scalar(select(func.count(t.id)).where(*base, t.created_at >= since)) or 0
    complaints = [d for (d,) in db.execute(select(t.description).where(
        *base, t.category == "complaint", t.created_at >= since))]
    themes = {k: sum(1 for c in complaints if any(w in c.lower() for w in words))
              for k, words in THEMES.items()}
    today = now.date()
    active_res = db.scalar(select(func.count(Reservation.id)).where(
        Reservation.organization_id == org, Reservation.hotel_id == hotel_id,
        Reservation.status.in_((ReservationStatus.confirmed, ReservationStatus.checked_in)),
        Reservation.check_in <= today, Reservation.check_out >= today)) or 0
    res_created = db.scalar(select(func.count(Reservation.id)).where(
        Reservation.organization_id == org, Reservation.hotel_id == hotel_id,
        Reservation.created_at >= since)) or 0
    res_cancelled = db.scalar(select(func.count(Reservation.id)).where(
        Reservation.organization_id == org, Reservation.hotel_id == hotel_id,
        Reservation.status == ReservationStatus.cancelled, Reservation.updated_at >= since)) or 0
    avg_rating = db.scalar(select(func.avg(GuestFeedback.rating)).where(
        GuestFeedback.organization_id == org, GuestFeedback.hotel_id == hotel_id,
        GuestFeedback.created_at >= since))
    fb = db.execute(
        select(func.date(GuestFeedback.created_at), func.avg(GuestFeedback.rating),
               func.count(GuestFeedback.id))
        .where(GuestFeedback.organization_id == org, GuestFeedback.hotel_id == hotel_id,
               GuestFeedback.created_at >= since)
        .group_by(func.date(GuestFeedback.created_at)).order_by(func.date(GuestFeedback.created_at))
    ).all()
    per_day = db.execute(
        select(func.date(t.created_at), func.count(t.id)).where(*base, t.created_at >= since)
        .group_by(func.date(t.created_at)).order_by(func.date(t.created_at))).all()
    return {
        "feedback_trend": [{"date": str(d), "avg_rating": round(float(a), 2), "count": c}
                           for d, a, c in fb],
        "tickets_per_day": [{"date": str(d), "count": c} for d, c in per_day],
        "window_days": days, "generated_at": now.isoformat(),
        "open_requests": sum(by_dept.values()) + (
            (db.scalar(select(func.count(t.id)).where(*base, t.status.in_(OPEN),
                                                      t.department_id.is_(None))) or 0)),
        "open_by_department": by_dept, "open_by_priority": by_priority, "overdue": overdue,
        "created_in_window": created,
        "avg_resolution_hours": round(avg_secs / 3600, 2) if avg_secs is not None else None,
        "escalations_in_window": escalations,
        "complaints_in_window": len(complaints), "complaint_themes": themes,
        "active_reservations": active_res, "reservations_created": res_created,
        "reservations_cancelled": res_cancelled,
        "avg_feedback_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
    }
