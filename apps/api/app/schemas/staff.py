import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.schemas.operations import TicketOut


class GuestBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None


class ReservationDetail(BaseModel):
    id: uuid.UUID
    confirmation_code: str
    status: str
    check_in: date
    check_out: date
    adults: int
    total_price: Decimal
    created_at: datetime
    guest: GuestBrief
    room_type_id: uuid.UUID
    room_type_name: str
    room_number: str | None


class ReservationChange(BaseModel):
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def _order(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self


class HistoryEvent(BaseModel):
    action: str
    actor_type: str
    actor_name: str | None
    created_at: datetime
    details: dict


class InventoryNight(BaseModel):
    night: date
    total: int
    booked: int
    rate: Decimal


class InventoryRow(BaseModel):
    room_type_id: uuid.UUID
    room_type_name: str
    nights: list[InventoryNight]


class StaffMember(BaseModel):
    id: uuid.UUID
    full_name: str
    role: str
    departments: list[str]


class DepartmentOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class TicketRow(TicketOut):
    department_name: str | None = None
    assignee_name: str | None = None
    created_at: datetime | None = None
    guest_name: str | None = None
