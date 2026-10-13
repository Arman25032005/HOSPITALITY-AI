import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.conversations.redact import mask_cards
from app.conversations.state import ConversationState
from app.llm.base import LLMError, LLMProvider
from app.models import (
    Conversation, Guest, GuestChannelIdentity, Hotel, Message, Organization,
)
from app.services.errors import ConflictError, NotFoundError

from pydantic import BaseModel


def resolve_hotel(db: Session, org_slug: str, hotel_slug: str) -> Hotel:
    hotel = db.scalar(select(Hotel).join(Organization, Organization.id == Hotel.organization_id)
                      .where(Organization.slug == org_slug, Hotel.slug == hotel_slug))
    if hotel is None:
        raise NotFoundError("Hotel not found")
    return hotel


def _retention(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.conversation_retention_days)


def open_conversation(db: Session, *, settings: Settings, hotel: Hotel, channel: str,
                      external_id: str | None = None) -> Conversation:
    """Find or create the guest identity for (org, channel, external_id) and its active
    conversation at this hotel. Web sessions pass no external_id and always get a new identity."""
    external_id = external_id or secrets.token_urlsafe(16)
    ident = db.scalar(select(GuestChannelIdentity).where(
        GuestChannelIdentity.organization_id == hotel.organization_id,
        GuestChannelIdentity.channel == channel,
        GuestChannelIdentity.external_id == external_id))
    if ident is None:
        guest = Guest(organization_id=hotel.organization_id, full_name="Guest")
        db.add(guest)
        db.flush()
        ident = GuestChannelIdentity(organization_id=hotel.organization_id, guest_id=guest.id,
                                     channel=channel, external_id=external_id)
        db.add(ident)
        db.flush()
    conv = db.scalar(select(Conversation).where(
        Conversation.organization_id == hotel.organization_id, Conversation.hotel_id == hotel.id,
        Conversation.guest_id == ident.guest_id, Conversation.channel == channel,
        Conversation.status.in_(("active", "handed_off")),
        Conversation.retention_expires_at > datetime.now(UTC))
        .order_by(Conversation.created_at.desc()))
    if conv is None:
        conv = Conversation(organization_id=hotel.organization_id, hotel_id=hotel.id,
                            guest_id=ident.guest_id, channel=channel,
                            state=ConversationState().dump(), status="active",
                            retention_expires_at=_retention(settings))
        db.add(conv)
    db.commit()
    return conv


def open_staff_conversation(db: Session, *, settings: Settings, user, hotel_id) -> Conversation:
    conv = db.scalar(select(Conversation).where(
        Conversation.organization_id == user.organization_id, Conversation.hotel_id == hotel_id,
        Conversation.staff_user_id == user.id, Conversation.channel == "staff",
        Conversation.status == "active"))
    if conv is None:
        conv = Conversation(organization_id=user.organization_id, hotel_id=hotel_id,
                            staff_user_id=user.id, channel="staff", state={}, status="active",
                            retention_expires_at=_retention(settings))
        db.add(conv)
        db.commit()
    return conv


def lock_conversation(db: Session, conversation_id: uuid.UUID) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id)
                     .with_for_update())
    if conv is None:
        raise NotFoundError("Conversation not found")
    return conv


def get_guest_conversation(db: Session, *, conversation_id, guest_id, org_id, hotel_id) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if (conv is None or conv.guest_id != guest_id or conv.organization_id != org_id
            or conv.hotel_id != hotel_id):
        raise NotFoundError("Conversation not found")
    if conv.retention_expires_at and conv.retention_expires_at <= datetime.now(UTC):
        raise NotFoundError("Conversation not found")
    return conv


def find_reply(db: Session, conversation_id, external_message_id: str) -> Message | None:
    user_msg = db.scalar(select(Message).where(
        Message.conversation_id == conversation_id, Message.role == "user",
        Message.external_message_id == external_message_id))
    if user_msg is None:
        return None
    return db.scalar(select(Message).where(
        Message.conversation_id == conversation_id, Message.role == "assistant",
        Message.meta["reply_to"].astext == external_message_id))


def add_message(db: Session, conv: Conversation, *, role: str, content: str,
                external_message_id: str | None = None, meta: dict | None = None) -> Message:
    msg = Message(organization_id=conv.organization_id, conversation_id=conv.id, role=role,
                  content=mask_cards(content), external_message_id=external_message_id,
                  meta=meta or {})
    db.add(msg)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError("Message is already being processed") from None
    return msg


def history(db: Session, conversation_id, limit: int) -> list[Message]:
    rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id)
                      .order_by(Message.created_at.desc(), Message.id).limit(limit)).all()
    return list(reversed(rows))


class _Summary(BaseModel):
    summary: str


def maybe_summarize(db: Session, conv: Conversation, settings: Settings,
                    llm: LLMProvider | None) -> None:
    """Fold messages older than the context window into `conv.summary` (best effort).

    The summary is background only: it never overrides policies or live records, and agents do
    not read facts about reservations or tickets from it."""
    window = settings.context_window_messages
    msgs = list(db.scalars(select(Message).where(Message.conversation_id == conv.id)
                           .order_by(Message.created_at, Message.id)))
    if llm is None or len(msgs) <= window * 2:
        return
    old = msgs[:-window]
    transcript = "\n".join(f"{m.role}: {m.content}" for m in old)[-6000:]
    try:
        out, _ = llm.structured(
            system="Summarize this hotel guest chat in at most 80 words. Mention what the guest "
                   "wants and what was resolved. Do not include personal data or invent facts.",
            messages=[{"role": "user", "content": transcript}], schema=_Summary, max_tokens=300)
    except LLMError:
        return
    conv.summary = out.summary[:1000]


def purge_expired(db: Session, now: datetime | None = None) -> int:
    """Delete conversations (and their messages) past their retention date."""
    now = now or datetime.now(UTC)
    ids = list(db.scalars(select(Conversation.id).where(Conversation.retention_expires_at <= now)))
    if not ids:
        return 0
    from app.models import AgentExecution

    db.execute(delete(Message).where(Message.conversation_id.in_(ids)))
    db.query(AgentExecution).filter(AgentExecution.conversation_id.in_(ids)).update(
        {AgentExecution.conversation_id: None}, synchronize_session=False)
    db.execute(delete(Conversation).where(Conversation.id.in_(ids)))
    db.commit()
    return len(ids)


MAX_VERIFY_FAILURES = 5


def apply_verification(db: Session, state: ConversationState, *, org_id, hotel_id, code: str,
                       last_name: str):
    """Code + surname check with an attempt cap per conversation. Mutates `state` (caller
    persists it). Returns the reservation or None; raises Forbidden once the cap is reached."""
    from app.services import reservations as resv
    from app.services.errors import ForbiddenError

    if state.verify_failures >= MAX_VERIFY_FAILURES:
        raise ForbiddenError("Too many failed verification attempts")
    res = resv.verify_reservation(db, org_id=org_id, hotel_id=hotel_id, code=code,
                                  last_name=last_name)
    if res is None:
        state.verify_failures += 1
        return None
    for lst in (state.verified_reservation_ids, state.linked_reservation_ids):
        if str(res.id) not in lst:
            lst.append(str(res.id))
    return res
