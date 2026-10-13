import uuid
from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Guest, Role, TicketPriority, TicketStatus, User
from app.schemas.operations import (
    AssignBody, GuestCreate, GuestOut, NoteBody, ReservationCreate, ReservationOut,
    RoomOptionOut, StatusChange, TicketCreate, TicketOut,
)
from app.services import reservations as resv
from app.services import tickets as tix
from app.services.access import get_hotel_for_user, require_role

router = APIRouter(prefix="/api/v1/hotels/{hotel_id}", tags=["operations"])

FRONT_OFFICE = (Role.admin, Role.manager, Role.receptionist)


@router.post("/guests", response_model=GuestOut, status_code=201)
def create_guest(hotel_id: uuid.UUID, body: GuestCreate, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> Guest:
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    guest = Guest(organization_id=user.organization_id, **body.model_dump())
    db.add(guest)
    db.commit()
    return guest


@router.get("/availability", response_model=list[RoomOptionOut])
def availability(hotel_id: uuid.UUID, check_in: date, check_out: date,
                 adults: int = Query(ge=1, le=10), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> list[RoomOptionOut]:
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    opts = resv.search_availability(db, org_id=user.organization_id, hotel_id=hotel_id,
                                    check_in=check_in, check_out=check_out, adults=adults)
    n = (check_out - check_in).days
    return [RoomOptionOut(room_type_id=o.room_type_id, code=o.code, name=o.name,
                          max_occupancy=o.max_occupancy, available=o.available, nights=n,
                          total_price=o.total_price) for o in opts]


@router.post("/reservations", response_model=ReservationOut, status_code=201)
def create_reservation(hotel_id: uuid.UUID, body: ReservationCreate,
                       idempotency_key: str | None = Header(default=None, max_length=100),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    return resv.create_reservation(
        db, org_id=user.organization_id, hotel_id=hotel_id, guest_id=body.guest_id,
        room_type_id=body.room_type_id, check_in=body.check_in, check_out=body.check_out,
        adults=body.adults, idempotency_key=idempotency_key, actor_id=str(user.id))


@router.get("/reservations/{reservation_id}", response_model=ReservationOut)
def get_reservation(hotel_id: uuid.UUID, reservation_id: uuid.UUID,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    return resv.get_reservation(db, org_id=user.organization_id, hotel_id=hotel_id,
                                reservation_id=reservation_id)


@router.post("/reservations/{reservation_id}/cancel", response_model=ReservationOut)
def cancel_reservation(hotel_id: uuid.UUID, reservation_id: uuid.UUID,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    return resv.cancel_reservation(db, org_id=user.organization_id, hotel_id=hotel_id,
                                   reservation_id=reservation_id, actor_id=str(user.id))


@router.post("/tickets", response_model=TicketOut, status_code=201)
def create_ticket(hotel_id: uuid.UUID, body: TicketCreate, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    return tix.create_ticket(db, org_id=user.organization_id, hotel_id=hotel_id,
                             actor_id=str(user.id), **body.model_dump())


@router.get("/tickets", response_model=list[TicketOut])
def list_tickets(hotel_id: uuid.UUID, status: TicketStatus | None = None,
                 department_id: uuid.UUID | None = None, priority: TicketPriority | None = None,
                 limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, Role.admin, Role.manager, Role.receptionist, Role.department_staff)
    return tix.list_tickets(db, user=user, hotel_id=hotel_id, status=status,
                            department_id=department_id, priority=priority, limit=limit,
                            offset=offset)


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(hotel_id: uuid.UUID, ticket_id: uuid.UUID, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, Role.admin, Role.manager, Role.receptionist, Role.department_staff)
    return tix.get_ticket(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id)


@router.post("/tickets/{ticket_id}/status", response_model=TicketOut)
def change_status(hotel_id: uuid.UUID, ticket_id: uuid.UUID, body: StatusChange,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, Role.admin, Role.manager, Role.receptionist, Role.department_staff)
    return tix.change_status(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id,
                             new_status=body.status)


@router.post("/tickets/{ticket_id}/assign", response_model=TicketOut)
def assign_ticket(hotel_id: uuid.UUID, ticket_id: uuid.UUID, body: AssignBody,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    return tix.assign(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id,
                      assignee_id=body.assignee_id)


@router.post("/tickets/{ticket_id}/notes", response_model=TicketOut)
def add_note(hotel_id: uuid.UUID, ticket_id: uuid.UUID, body: NoteBody,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, Role.admin, Role.manager, Role.receptionist, Role.department_staff)
    return tix.add_note(db, user=user, hotel_id=hotel_id, ticket_id=ticket_id, note=body.note)
