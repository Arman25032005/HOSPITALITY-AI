import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import (
    AuditEvent, Conversation, Department, Guest, Reservation, ReservationStatus, Role, Room,
    RoomInventory, RoomType, StaffAssignment, TicketPriority, TicketStatus, User,
)
from app.schemas.staff import (
    DepartmentOut, GuestBrief, HistoryEvent, InventoryNight, InventoryRow, ReservationChange,
    ReservationDetail, StaffMember, TicketRow,
)
from app.services import reservations as resv
from app.services import tickets as tix
from app.services.access import get_hotel_for_user, require_role
from app.services.errors import NotFoundError, ValidationFailed

router = APIRouter(prefix="/api/v1/hotels/{hotel_id}", tags=["staff-views"])
FRONT_OFFICE = (Role.admin, Role.manager, Role.receptionist)
ALL_STAFF = (Role.admin, Role.manager, Role.receptionist, Role.department_staff)


def _escape_like(s: str) -> str:
    return "%" + s.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _details(db: Session, rows) -> list[ReservationDetail]:
    out = []
    for r, g, rt, room_no in rows:
        out.append(ReservationDetail(
            id=r.id, confirmation_code=r.confirmation_code, status=r.status.value,
            check_in=r.check_in, check_out=r.check_out, adults=r.adults,
            total_price=r.total_price, created_at=r.created_at,
            guest=GuestBrief(id=g.id, full_name=g.full_name, email=g.email, phone=g.phone),
            room_type_id=rt.id, room_type_name=rt.name, room_number=room_no))
    return out


def _res_query(user: User, hotel_id):
    return (select(Reservation, Guest, RoomType, Room.number)
            .join(Guest, Guest.id == Reservation.guest_id)
            .join(RoomType, RoomType.id == Reservation.room_type_id)
            .outerjoin(Room, Room.id == Reservation.room_id)
            .where(Reservation.organization_id == user.organization_id,
                   Reservation.hotel_id == hotel_id))


@router.get("/reservations", response_model=list[ReservationDetail])
def search_reservations(
    hotel_id: uuid.UUID, q: str | None = None, status: ReservationStatus | None = None,
    arrival_from: date | None = None, arrival_to: date | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    query = _res_query(user, hotel_id)
    if q and q.strip():
        like = _escape_like(q)
        query = query.where(Reservation.confirmation_code.ilike(like, escape="\\")
                            | Guest.full_name.ilike(like, escape="\\"))
    if status:
        query = query.where(Reservation.status == status)
    if arrival_from:
        query = query.where(Reservation.check_in >= arrival_from)
    if arrival_to:
        query = query.where(Reservation.check_in <= arrival_to)
    rows = db.execute(query.order_by(Reservation.check_in.desc(), Reservation.created_at.desc())
                      .limit(limit).offset(offset)).all()
    return _details(db, rows)


def _one(db: Session, user: User, hotel_id, reservation_id) -> ReservationDetail:
    row = db.execute(_res_query(user, hotel_id).where(Reservation.id == reservation_id)).first()
    if row is None:
        raise NotFoundError("Reservation not found")
    return _details(db, [row])[0]


@router.get("/reservations/{reservation_id}/history", response_model=list[HistoryEvent])
def reservation_history(hotel_id: uuid.UUID, reservation_id: uuid.UUID,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    _one(db, user, hotel_id, reservation_id)  # tenant + existence check
    return _history(db, user, hotel_id, "reservation", reservation_id)


@router.post("/reservations/{reservation_id}/change", response_model=ReservationDetail)
def change_reservation(hotel_id: uuid.UUID, reservation_id: uuid.UUID, body: ReservationChange,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    resv.change_reservation(db, org_id=user.organization_id, hotel_id=hotel_id,
                            reservation_id=reservation_id, check_in=body.check_in,
                            check_out=body.check_out, adults=body.adults, actor_id=str(user.id))
    return _one(db, user, hotel_id, reservation_id)


@router.get("/inventory", response_model=list[InventoryRow])
def inventory(hotel_id: uuid.UUID, from_: date = Query(alias="from", default=None),
              to: date = Query(default=None), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    start = from_ or date.today()
    end = to or start + timedelta(days=13)
    if end < start or (end - start).days > 62:
        raise ValidationFailed("Range must be between 0 and 62 days")
    rows = db.execute(
        select(RoomType.id, RoomType.name, RoomInventory.night, RoomInventory.total,
               RoomInventory.booked, RoomInventory.rate)
        .join(RoomInventory, RoomInventory.room_type_id == RoomType.id)
        .where(RoomType.organization_id == user.organization_id, RoomType.hotel_id == hotel_id,
               RoomInventory.night >= start, RoomInventory.night <= end)
        .order_by(RoomType.base_rate, RoomInventory.night)).all()
    out: dict[uuid.UUID, InventoryRow] = {}
    for rid, name, night, total, booked, rate in rows:
        out.setdefault(rid, InventoryRow(room_type_id=rid, room_type_name=name, nights=[])
                       ).nights.append(InventoryNight(night=night, total=total, booked=booked,
                                                      rate=rate))
    return list(out.values())


@router.get("/departments", response_model=list[DepartmentOut])
def departments(hotel_id: uuid.UUID, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ALL_STAFF)
    return list(db.scalars(select(Department).where(
        Department.organization_id == user.organization_id, Department.hotel_id == hotel_id)
        .order_by(Department.name)))


@router.get("/staff", response_model=list[StaffMember])
def staff(hotel_id: uuid.UUID, user: User = Depends(get_current_user),
          db: Session = Depends(get_db)):
    """Assignable colleagues at this hotel (assigned here, or org-wide admins/managers)."""
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    rows = db.execute(
        select(User, Department.name)
        .outerjoin(StaffAssignment, (StaffAssignment.user_id == User.id)
                   & (StaffAssignment.hotel_id == hotel_id))
        .outerjoin(Department, Department.id == StaffAssignment.department_id)
        .where(User.organization_id == user.organization_id, User.is_active.is_(True),
               (User.role.in_((Role.admin, Role.manager))) | (StaffAssignment.id.is_not(None)))
        .order_by(User.full_name)).all()
    members: dict[uuid.UUID, StaffMember] = {}
    for u, dept in rows:
        m = members.setdefault(u.id, StaffMember(id=u.id, full_name=u.full_name,
                                                 role=u.role.value, departments=[]))
        if dept and dept not in m.departments:
            m.departments.append(dept)
    return list(members.values())


def _history(db: Session, user: User, hotel_id, entity_type: str, entity_id) -> list[HistoryEvent]:
    rows = db.execute(
        select(AuditEvent, User.full_name)
        .outerjoin(User, cast(User.id, String) == AuditEvent.actor_id)
        .where(AuditEvent.organization_id == user.organization_id,
               AuditEvent.hotel_id == hotel_id, AuditEvent.entity_type == entity_type,
               AuditEvent.entity_id == str(entity_id))
        .order_by(AuditEvent.created_at)).all()
    return [HistoryEvent(action=a.action, actor_type=a.actor_type,
                         actor_name=name if a.actor_type == "user" else None,
                         created_at=a.created_at, details=a.details) for a, name in rows]


@router.get("/tickets/{ticket_id}/history", response_model=list[HistoryEvent])
def ticket_history(hotel_id: uuid.UUID, ticket_id: uuid.UUID,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ALL_STAFF)
    tix.get_ticket(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id)  # visibility check
    return _history(db, user, hotel_id, "service_request", ticket_id)


@router.get("/tickets/{ticket_id}/conversation")
def ticket_conversation(hotel_id: uuid.UUID, ticket_id: uuid.UUID,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The guest conversation that produced this ticket, for roles allowed to read guest chats."""
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    tix.get_ticket(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id)
    cid = db.scalar(select(Conversation.id).where(
        Conversation.organization_id == user.organization_id, Conversation.hotel_id == hotel_id,
        Conversation.state["linked_ticket_ids"].contains([str(ticket_id)])))
    return {"conversation_id": str(cid) if cid else None}


@router.get("/tickets-search", response_model=list[TicketRow])
def search_tickets(
    hotel_id: uuid.UUID, q: str | None = None, status: TicketStatus | None = None,
    department_id: uuid.UUID | None = None, priority: TicketPriority | None = None,
    assigned_to: uuid.UUID | None = None, created_from: date | None = None,
    created_to: date | None = None, overdue: bool = False,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Ticket list for the dashboard: filters plus department / assignee / guest names."""
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ALL_STAFF)
    start = datetime.combine(created_from, datetime.min.time(), tzinfo=UTC) if created_from else None
    end = (datetime.combine(created_to, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
           if created_to else None)
    tickets = tix.list_tickets(db, user=user, hotel_id=hotel_id, status=status,
                               department_id=department_id, priority=priority, limit=limit,
                               offset=offset, q=q, created_from=start, created_to=end,
                               assigned_to=assigned_to, overdue=overdue)
    depts = {d.id: d.name for d in db.scalars(select(Department).where(
        Department.hotel_id == hotel_id))}
    users = {u.id: u.full_name for u in db.scalars(select(User).where(
        User.organization_id == user.organization_id,
        User.id.in_({t.assigned_to for t in tickets if t.assigned_to})))}
    guests = {g.id: g.full_name for g in db.scalars(select(Guest).where(
        Guest.organization_id == user.organization_id,
        Guest.id.in_({t.guest_id for t in tickets if t.guest_id})))}
    return [TicketRow.model_validate(
        {**{c: getattr(t, c) for c in TicketRow.model_fields if hasattr(t, c)},
         "department_name": depts.get(t.department_id), "assignee_name": users.get(t.assigned_to),
         "guest_name": guests.get(t.guest_id)}) for t in tickets]
