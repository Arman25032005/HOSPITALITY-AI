import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import handle_message
from app.api.deps import get_current_user
from app.api.routes.knowledge import get_embedder_dep, get_store_dep
from app.config import Settings, get_settings
from app.conversations import service as convs
from app.conversations.state import ConversationState
from app.db.session import get_db
from app.models import Conversation, Guest, Role, User
from app.rag.embeddings import EmbeddingProvider
from app.rag.vectorstore import VectorStore
from app.schemas.chat import (
    ApprovalOut, CheckInIn, ChatIn, ChatOut, ConversationOut, DecideIn, MessageOut,
    StaffMessageIn,
)
from app.schemas.operations import ReservationOut
from app.services import analytics, approvals, audit
from app.services import reservations as resv
from app.services.access import get_hotel_for_user, require_role
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/v1/hotels/{hotel_id}", tags=["staff-ops"])
FRONT_OFFICE = (Role.admin, Role.manager, Role.receptionist)
MANAGERS = (Role.admin, Role.manager)


@router.post("/ops/chat", response_model=ChatOut)
def ops_chat(hotel_id: uuid.UUID, body: ChatIn, user: User = Depends(get_current_user),
             db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
             embedder: EmbeddingProvider = Depends(get_embedder_dep),
             store: VectorStore = Depends(get_store_dep)):
    hotel = get_hotel_for_user(db, user, hotel_id)
    require_role(user, *MANAGERS)
    conv = convs.open_staff_conversation(db, settings=settings, user=user, hotel_id=hotel_id)
    reply, msg = handle_message(
        db, settings=settings, conversation_id=conv.id, text=body.text,
        client_message_id=body.client_message_id, actor="staff", hotel=hotel, llm=None,
        embedder=embedder, store=store, user=user)
    return ChatOut(reply=reply.text, outcome=reply.outcome, awaiting_confirmation=False,
                   citations=[], ticket_numbers=[], reservation_codes=[], message_id=msg.id)


@router.get("/analytics/summary")
def analytics_summary(hotel_id: uuid.UUID, days: int = Query(7, ge=1, le=90),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    return analytics.summary(db, user=user, hotel_id=hotel_id, days=days)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(hotel_id: uuid.UUID, status: str | None = None,
                       limit: int = Query(50, ge=1, le=200), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    q = select(Conversation, Guest.full_name).outerjoin(Guest, Guest.id == Conversation.guest_id)\
        .where(Conversation.organization_id == user.organization_id,
               Conversation.hotel_id == hotel_id, Conversation.channel != "staff")
    if status:
        q = q.where(Conversation.status == status)
    rows = db.execute(q.order_by(Conversation.updated_at.desc()).limit(limit)).all()
    return [ConversationOut(id=c.id, channel=c.channel, status=c.status, guest_name=n,
                            last_activity=c.updated_at,
                            handoff_ticket=(c.state or {}).get("handoff_ticket"))
            for c, n in rows]


def _conv_for_staff(db: Session, user: User, hotel_id, conversation_id) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if (conv is None or conv.organization_id != user.organization_id
            or conv.hotel_id != hotel_id or conv.channel == "staff"):
        raise NotFoundError("Conversation not found")
    return conv


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def conversation_messages(hotel_id: uuid.UUID, conversation_id: uuid.UUID,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    conv = _conv_for_staff(db, user, hotel_id, conversation_id)
    audit.record(db, org_id=conv.organization_id, hotel_id=hotel_id,
                 action="conversation.viewed", actor_type="user", actor_id=str(user.id),
                 entity_type="conversation", entity_id=str(conv.id))
    db.commit()
    return [MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at,
                       citations=m.meta.get("citations", []))
            for m in convs.history(db, conv.id, 500)]


@router.post("/conversations/{conversation_id}/staff-messages", response_model=MessageOut,
             status_code=201)
def staff_message(hotel_id: uuid.UUID, conversation_id: uuid.UUID, body: StaffMessageIn,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    conv = _conv_for_staff(db, user, hotel_id, conversation_id)
    msg = convs.add_message(db, conv, role="staff", content=body.text,
                            meta={"staff_user_id": str(user.id)})
    audit.record(db, org_id=conv.organization_id, hotel_id=hotel_id,
                 action="conversation.staff_message", actor_type="user", actor_id=str(user.id),
                 entity_type="conversation", entity_id=str(conv.id))
    db.commit()
    return MessageOut(id=msg.id, role=msg.role, content=msg.content, created_at=msg.created_at)


@router.post("/conversations/{conversation_id}/release", response_model=ConversationOut)
def release_conversation(hotel_id: uuid.UUID, conversation_id: uuid.UUID,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hand a conversation back to the assistant."""
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    conv = convs.lock_conversation(db, _conv_for_staff(db, user, hotel_id, conversation_id).id)
    state = ConversationState.load(conv.state)
    state.handoff_ticket = None
    conv.state, conv.status = state.dump(), "active"
    audit.record(db, org_id=conv.organization_id, hotel_id=hotel_id,
                 action="conversation.released", actor_type="user", actor_id=str(user.id),
                 entity_type="conversation", entity_id=str(conv.id))
    db.commit()
    name = db.scalar(select(Guest.full_name).where(Guest.id == conv.guest_id))
    return ConversationOut(id=conv.id, channel=conv.channel, status=conv.status, guest_name=name,
                           last_activity=conv.updated_at, handoff_ticket=None)


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(hotel_id: uuid.UUID, status: str | None = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    return approvals.list_approvals(db, user=user, hotel_id=hotel_id, status=status)


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
def decide_approval(hotel_id: uuid.UUID, approval_id: uuid.UUID, body: DecideIn,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    return approvals.decide(db, user=user, hotel_id=hotel_id, approval_id=approval_id,
                            approve=body.approve, note=body.note,
                            approved_until=body.approved_until)


@router.post("/reservations/{reservation_id}/check-in", response_model=ReservationOut)
def check_in(hotel_id: uuid.UUID, reservation_id: uuid.UUID, body: CheckInIn | None = None,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *FRONT_OFFICE)
    return resv.check_in_reservation(db, org_id=user.organization_id, hotel_id=hotel_id,
                                     reservation_id=reservation_id, actor_id=str(user.id),
                                     room_number=body.room_number if body else None)
