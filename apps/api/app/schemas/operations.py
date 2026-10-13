import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import ReservationStatus, TicketPriority, TicketStatus


class GuestCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=32)


class GuestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    full_name: str


class StayQuery(BaseModel):
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def _order(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class RoomOptionOut(BaseModel):
    room_type_id: uuid.UUID
    code: str
    name: str
    max_occupancy: int
    available: int
    nights: int
    total_price: Decimal


class ReservationCreate(StayQuery):
    guest_id: uuid.UUID
    room_type_id: uuid.UUID


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    confirmation_code: str
    hotel_id: uuid.UUID
    guest_id: uuid.UUID
    room_type_id: uuid.UUID
    check_in: date
    check_out: date
    adults: int
    total_price: Decimal
    status: ReservationStatus


class TicketCreate(BaseModel):
    category: str = Field(max_length=50)
    description: str = Field(min_length=1, max_length=4000)
    priority: TicketPriority = TicketPriority.normal
    guest_id: uuid.UUID | None = None
    room_number: str | None = Field(default=None, max_length=20)


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ticket_number: str
    category: str
    description: str
    room_number: str | None
    priority: TicketPriority
    status: TicketStatus
    department_id: uuid.UUID | None
    assigned_to: uuid.UUID | None
    due_at: datetime | None
    resolved_at: datetime | None
    internal_notes: list


class StatusChange(BaseModel):
    status: TicketStatus


class AssignBody(BaseModel):
    assignee_id: uuid.UUID


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
