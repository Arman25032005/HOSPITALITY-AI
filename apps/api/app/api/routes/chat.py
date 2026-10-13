
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import handle_message
from app.api.deps import GuestSession, get_guest_session
from app.api.routes.knowledge import get_embedder_dep, get_store_dep
from app.config import Settings, get_settings
from app.conversations import service as convs
from app.conversations.ratelimit import limiter
from app.conversations.state import ConversationState
from app.core.security import create_guest_token
from app.db.session import get_db
from app.llm.base import LLMProvider
from app.llm.factory import get_llm
from app.models import GuestFeedback, Hotel, Message, Organization
from app.rag.embeddings import EmbeddingProvider
from app.rag.vectorstore import VectorStore
from app.schemas.chat import (
    ChatIn, ChatOut, FeedbackIn, MessageOut, PublicHotel, SessionCreate, SessionOut, VerifyIn, VerifyOut,
)

router = APIRouter(prefix="/api/v1", tags=["guest-chat"])

_llm_cache: dict[tuple, LLMProvider | None] = {}


def get_llm_dep(settings: Settings = Depends(get_settings)) -> LLMProvider | None:
    key = (settings.llm_provider, settings.llm_model, settings.llm_base_url, settings.llm_api_key)
    if key not in _llm_cache:
        _llm_cache[key] = get_llm(settings)
    return _llm_cache[key]


def _limit(key: str, limit: int) -> None:
    if not limiter.allow(key, limit):
        raise HTTPException(429, "Too many requests; please slow down", headers={"Retry-After": "60"})


@router.get("/public/hotels", response_model=list[PublicHotel])
def public_hotels(db: Session = Depends(get_db)):
    rows = db.execute(select(Organization.slug, Hotel.slug, Hotel.name)
                      .join(Hotel, Hotel.organization_id == Organization.id)
                      .order_by(Hotel.name)).all()
    return [PublicHotel(org_slug=o, hotel_slug=h, name=n) for o, h, n in rows]


@router.post("/chat/sessions", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreate, request: Request, db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    ip = request.client.host if request.client else "unknown"
    _limit(f"session:{ip}", settings.chat_session_rate_per_minute)
    hotel = convs.resolve_hotel(db, body.org_slug, body.hotel_slug)
    conv = convs.open_conversation(db, settings=settings, hotel=hotel, channel="web")
    return SessionOut(token=create_guest_token(conv.guest_id, hotel.organization_id, hotel.id,
                                               conv.id),
                      conversation_id=conv.id, hotel_name=hotel.name)


def _guest_conv(db: Session, g: GuestSession):
    return convs.get_guest_conversation(db, conversation_id=g.conversation_id,
                                        guest_id=g.guest_id, org_id=g.org_id, hotel_id=g.hotel_id)


@router.post("/chat/messages", response_model=ChatOut)
def post_message(body: ChatIn, g: GuestSession = Depends(get_guest_session),
                 db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                 llm: LLMProvider | None = Depends(get_llm_dep),
                 embedder: EmbeddingProvider = Depends(get_embedder_dep),
                 store: VectorStore = Depends(get_store_dep)):
    _limit(f"msg:{g.guest_id}", settings.chat_message_rate_per_minute)
    conv = _guest_conv(db, g)
    hotel = db.get(Hotel, conv.hotel_id)
    reply, msg = handle_message(
        db, settings=settings, conversation_id=conv.id, text=body.text,
        client_message_id=body.client_message_id, actor="guest", hotel=hotel, llm=llm,
        embedder=embedder, store=store)
    return ChatOut(reply=reply.text, outcome=reply.outcome,
                   awaiting_confirmation=reply.awaiting_confirmation, citations=reply.citations,
                   ticket_numbers=reply.ticket_numbers, reservation_codes=reply.reservation_codes,
                   message_id=msg.id)


@router.get("/chat/messages", response_model=list[MessageOut])
def get_messages(g: GuestSession = Depends(get_guest_session), db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    conv = _guest_conv(db, g)
    msgs = convs.history(db, conv.id, 200)
    return [MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at,
                       citations=m.meta.get("citations", [])) for m in msgs
            if m.role in ("user", "assistant", "staff")]


@router.post("/chat/verify", response_model=VerifyOut)
def verify(body: VerifyIn, g: GuestSession = Depends(get_guest_session),
           db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    _limit(f"verify:{g.guest_id}", 10)
    conv = convs.lock_conversation(db, _guest_conv(db, g).id)
    state = ConversationState.load(conv.state)
    res = convs.apply_verification(db, state, org_id=g.org_id, hotel_id=g.hotel_id,
                                   code=body.confirmation_code.upper(), last_name=body.last_name)
    conv.state = state.dump()
    db.commit()
    return VerifyOut(verified=res is not None,
                     attempts_left=max(0, convs.MAX_VERIFY_FAILURES - state.verify_failures))


@router.post("/chat/messages/{message_id}/feedback", status_code=201)
def message_feedback(message_id: uuid.UUID, body: FeedbackIn,
                     g: GuestSession = Depends(get_guest_session), db: Session = Depends(get_db)):
    """Rate an assistant reply. Only replies in the caller's own conversation are eligible."""
    conv = _guest_conv(db, g)
    msg = db.get(Message, message_id)
    if msg is None or msg.conversation_id != conv.id or msg.role != "assistant":
        raise HTTPException(404, "Message not found")
    db.add(GuestFeedback(organization_id=g.org_id, hotel_id=g.hotel_id, guest_id=g.guest_id,
                         message_id=msg.id, rating=body.rating, comment=body.comment))
    db.commit()
    return {"status": "recorded"}
