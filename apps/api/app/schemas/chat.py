import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    org_slug: str = Field(min_length=1, max_length=100)
    hotel_slug: str = Field(min_length=1, max_length=100)


class SessionOut(BaseModel):
    token: str
    conversation_id: uuid.UUID
    hotel_name: str


class PublicHotel(BaseModel):
    org_slug: str
    hotel_slug: str
    name: str


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: str = Field(min_length=1, max_length=128)


class ChatOut(BaseModel):
    reply: str
    outcome: str
    awaiting_confirmation: bool
    citations: list[dict]
    ticket_numbers: list[str]
    reservation_codes: list[str]
    message_id: uuid.UUID


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    citations: list[dict] = []


class VerifyIn(BaseModel):
    confirmation_code: str = Field(pattern=r"^R-[A-Za-z0-9]{8}$")
    last_name: str = Field(min_length=1, max_length=60)


class VerifyOut(BaseModel):
    verified: bool
    attempts_left: int


class StaffMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    guest_name: str | None
    last_activity: datetime
    handoff_ticket: str | None


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)
    approved_until: str | None = Field(default=None, max_length=40)


class ApprovalOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    payload: dict
    created_at: datetime
    decided_at: datetime | None


class CheckInIn(BaseModel):
    room_number: str | None = Field(default=None, max_length=20)


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)
