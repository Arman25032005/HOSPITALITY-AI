import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Guest, Role, ServiceRequest, TicketPriority, TicketStatus, User,
)
from app.core import metrics
from app.services import audit
from app.services.notifications import notify
from app.services.access import department_by_code, user_department_ids
from app.services.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailed

CATEGORY_DEPARTMENT = {
    "maintenance": "maintenance",
    "housekeeping": "housekeeping",
    "room_service": "food_beverage",
    "concierge": "front_desk",
    "complaint": "front_desk",
    "emergency": "front_desk",
    "handoff": "front_desk",
}
SLA_HOURS = {TicketPriority.urgent: 1, TicketPriority.high: 4, TicketPriority.normal: 24,
             TicketPriority.low: 72}

S = TicketStatus
ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    S.open: {S.assigned, S.in_progress, S.escalated, S.closed},
    S.assigned: {S.open, S.in_progress, S.escalated},
    S.in_progress: {S.assigned, S.resolved, S.escalated},
    S.escalated: {S.assigned, S.in_progress, S.resolved},
    S.resolved: {S.in_progress, S.closed},
    S.closed: set(),
}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _ticket_number() -> str:
    return "T-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def create_ticket(
    db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID, category: str, description: str,
    priority: TicketPriority = TicketPriority.normal, guest_id: uuid.UUID | None = None,
    room_number: str | None = None, actor_type: str = "user", actor_id: str | None = None,
    reservation_id: uuid.UUID | None = None, initial_status: TicketStatus = TicketStatus.open,
    notes: list[str] | None = None,
) -> ServiceRequest:
    if category not in CATEGORY_DEPARTMENT:
        raise ValidationFailed(f"Unknown category; expected one of {sorted(CATEGORY_DEPARTMENT)}")
    if not description.strip():
        raise ValidationFailed("description is required")
    if guest_id is not None:
        guest = db.get(Guest, guest_id)
        if guest is None or guest.organization_id != org_id:
            raise NotFoundError("Guest not found")
    dept = department_by_code(db, hotel_id, CATEGORY_DEPARTMENT[category])
    now = datetime.now(UTC)
    ticket = ServiceRequest(
        organization_id=org_id, hotel_id=hotel_id, guest_id=guest_id,
        department_id=dept.id if dept else None, ticket_number=_ticket_number(),
        category=category, description=description.strip(), room_number=room_number,
        priority=priority, status=initial_status, reservation_id=reservation_id,
        due_at=now + timedelta(hours=SLA_HOURS[priority]),
        internal_notes=[{"author_id": actor_id or actor_type, "at": now.isoformat(), "text": n}
                        for n in (notes or [])],
    )
    db.add(ticket)
    db.flush()
    audit.record(db, org_id=org_id, hotel_id=hotel_id, action="ticket.created",
                 actor_type=actor_type, actor_id=actor_id, entity_type="service_request",
                 entity_id=str(ticket.id),
                 details={"number": ticket.ticket_number, "category": category,
                          "priority": priority.value})
    if initial_status == TicketStatus.escalated:
        audit.record(db, org_id=org_id, hotel_id=hotel_id, action="ticket.escalated",
                     actor_type=actor_type, actor_id=actor_id, entity_type="service_request",
                     entity_id=str(ticket.id), details={"reason": (notes or ["unspecified"])[0]})
    if (initial_status == TicketStatus.escalated or priority == TicketPriority.urgent
            or category in ("complaint", "emergency", "handoff")):
        critical = category == "emergency" or priority == TicketPriority.urgent
        label = {"emergency": "Emergency reported", "complaint": "Guest complaint",
                 "handoff": "Guest needs a person"}.get(category, f"Urgent {category} request")
        notify(db, org_id=org_id, hotel_id=hotel_id, kind=f"ticket_{category}",
               title=f"{label} ({ticket.ticket_number})",
               severity="critical" if critical else "warning", entity_type="service_request",
               entity_id=str(ticket.id), department_id=ticket.department_id)
    db.commit()
    metrics.TICKETS.labels(category).inc()
    return ticket


def _visible_to(db: Session, user: User, ticket: ServiceRequest) -> bool:
    if user.role in (Role.admin, Role.manager, Role.receptionist):
        return True
    return ticket.department_id in user_department_ids(db, user, ticket.hotel_id)


def get_ticket(db: Session, *, user: User, hotel_id: uuid.UUID, ticket_id: uuid.UUID,
               lock: bool = False) -> ServiceRequest:
    q = select(ServiceRequest).where(ServiceRequest.id == ticket_id)
    if lock:
        q = q.with_for_update()
    t = db.scalar(q)
    if (t is None or t.organization_id != user.organization_id or t.hotel_id != hotel_id
            or not _visible_to(db, user, t)):
        raise NotFoundError("Ticket not found")
    return t


def list_tickets(
    db: Session, *, user: User, hotel_id: uuid.UUID, status: TicketStatus | None = None,
    department_id: uuid.UUID | None = None, priority: TicketPriority | None = None,
    limit: int = 50, offset: int = 0, q: str | None = None, created_from=None, created_to=None,
    assigned_to: uuid.UUID | None = None, overdue: bool = False,
) -> list[ServiceRequest]:
    text_query = q
    q = select(ServiceRequest).where(
        ServiceRequest.organization_id == user.organization_id,
        ServiceRequest.hotel_id == hotel_id,
    )
    if text_query and text_query.strip():
        like = "%" + text_query.strip().replace("%", r"\%").replace("_", r"\_") + "%"
        q = q.where(ServiceRequest.ticket_number.ilike(like, escape="\\")
                    | ServiceRequest.description.ilike(like, escape="\\")
                    | ServiceRequest.room_number.ilike(like, escape="\\"))
    if user.role == Role.department_staff:
        q = q.where(ServiceRequest.department_id.in_(user_department_ids(db, user, hotel_id)))
    if status:
        q = q.where(ServiceRequest.status == status)
    if department_id:
        q = q.where(ServiceRequest.department_id == department_id)
    if priority:
        q = q.where(ServiceRequest.priority == priority)
    if assigned_to:
        q = q.where(ServiceRequest.assigned_to == assigned_to)
    if created_from:
        q = q.where(ServiceRequest.created_at >= created_from)
    if created_to:
        q = q.where(ServiceRequest.created_at < created_to)
    if overdue:
        q = q.where(ServiceRequest.due_at < datetime.now(UTC),
                    ServiceRequest.status.in_((TicketStatus.open, TicketStatus.assigned,
                                               TicketStatus.in_progress, TicketStatus.escalated)))
    q = q.order_by(ServiceRequest.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return list(db.scalars(q))


def change_status(db: Session, *, user: User, hotel_id: uuid.UUID, ticket_id: uuid.UUID,
                  new_status: TicketStatus) -> ServiceRequest:
    t = get_ticket(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id, lock=True)
    if new_status == t.status:
        return t
    if new_status not in ALLOWED_TRANSITIONS[t.status]:
        raise ConflictError(f"Cannot move ticket from {t.status.value} to {new_status.value}")
    old = t.status
    t.status = new_status
    if new_status == TicketStatus.resolved:
        t.resolved_at = datetime.now(UTC)
    elif new_status in (TicketStatus.in_progress, TicketStatus.assigned):
        t.resolved_at = None
    audit.record(db, org_id=t.organization_id, hotel_id=hotel_id, action="ticket.status_changed",
                 actor_type="user", actor_id=str(user.id), entity_type="service_request",
                 entity_id=str(t.id), details={"from": old.value, "to": new_status.value})
    if new_status == TicketStatus.escalated:
        audit.record(db, org_id=t.organization_id, hotel_id=hotel_id, action="ticket.escalated",
                     actor_type="user", actor_id=str(user.id), entity_type="service_request",
                     entity_id=str(t.id), details={"reason": "manual escalation"})
        notify(db, org_id=t.organization_id, hotel_id=hotel_id, kind="ticket_escalated",
               title=f"Ticket escalated ({t.ticket_number})", severity="warning",
               entity_type="service_request", entity_id=str(t.id), department_id=t.department_id)
    db.commit()
    return t


def assign(db: Session, *, user: User, hotel_id: uuid.UUID, ticket_id: uuid.UUID,
           assignee_id: uuid.UUID) -> ServiceRequest:
    if user.role not in (Role.admin, Role.manager, Role.receptionist):
        raise ForbiddenError("Insufficient permissions")
    t = get_ticket(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id, lock=True)
    assignee = db.get(User, assignee_id)
    if assignee is None or assignee.organization_id != user.organization_id or not assignee.is_active:
        raise NotFoundError("Assignee not found")
    t.assigned_to = assignee.id
    if t.status == TicketStatus.open:
        t.status = TicketStatus.assigned
    audit.record(db, org_id=t.organization_id, hotel_id=hotel_id, action="ticket.assigned",
                 actor_type="user", actor_id=str(user.id), entity_type="service_request",
                 entity_id=str(t.id), details={"assignee": str(assignee.id)})
    db.commit()
    return t


def add_note(db: Session, *, user: User, hotel_id: uuid.UUID, ticket_id: uuid.UUID,
             note: str) -> ServiceRequest:
    if not note.strip():
        raise ValidationFailed("note is required")
    t = get_ticket(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id, lock=True)
    t.internal_notes = [*t.internal_notes, {
        "author_id": str(user.id), "at": datetime.now(UTC).isoformat(), "text": note.strip()}]
    audit.record(db, org_id=t.organization_id, hotel_id=hotel_id, action="ticket.note_added",
                 actor_type="user", actor_id=str(user.id), entity_type="service_request",
                 entity_id=str(t.id))
    db.commit()
    return t
