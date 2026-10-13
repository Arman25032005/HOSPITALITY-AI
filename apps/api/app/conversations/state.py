import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class PendingAction(BaseModel):
    """A consequential action awaiting an explicit YES from the guest. Parameters are fixed at
    proposal time; confirming executes exactly these and nothing the guest says later."""

    kind: Literal["create_reservation", "cancel_reservation", "handoff"]
    nonce: str = Field(default_factory=lambda: uuid.uuid4().hex)
    params: dict = Field(default_factory=dict)
    summary: str = ""
    expires_at: datetime

    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


class ReservationDraft(BaseModel):
    check_in: str | None = None  # ISO dates
    check_out: str | None = None
    adults: int | None = None
    room_type_id: str | None = None
    room_type_name: str | None = None
    guest_name: str | None = None
    offered: dict[str, str] = Field(default_factory=dict)  # lowercase name/code -> room_type_id


class ConversationState(BaseModel):
    verified_reservation_ids: list[str] = Field(default_factory=list)
    verify_failures: int = 0
    linked_reservation_ids: list[str] = Field(default_factory=list)
    linked_ticket_ids: list[str] = Field(default_factory=list)
    linked_approval_ids: list[str] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    active_flow: Literal["reservation"] | None = None
    draft: ReservationDraft = Field(default_factory=ReservationDraft)
    consecutive_abstains: int = 0
    last_intent: str | None = None
    handoff_ticket: str | None = None

    @classmethod
    def load(cls, raw: dict | None) -> "ConversationState":
        return cls.model_validate(raw or {})

    def dump(self) -> dict:
        return self.model_dump(mode="json")
