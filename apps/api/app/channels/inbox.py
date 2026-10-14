import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.orchestrator import handle_message
from app.channels import delivery
from app.channels.base import InboundMessage
from app.channels.registry import CHANNEL_FOR_PROVIDER
from app.config import Settings
from app.core import metrics
from app.conversations import service as convs
from app.llm.base import LLMProvider
from app.models import ChannelEvent, Hotel
from app.rag.embeddings import EmbeddingProvider
from app.rag.vectorstore import VectorStore

log = logging.getLogger("hospitality.channels")
STALE_AFTER = timedelta(seconds=120)
UNSUPPORTED_REPLY = ("I can only read text messages here. Please type your request, or say "
                     "'speak to a person' and I'll connect you with our front desk team.")


def _claim(db: Session, inbound: InboundMessage, hotel: Hotel) -> tuple[ChannelEvent | None, str]:
    """Record the delivery. Returns (event, 'new'|'reclaimed') or (None, 'duplicate')."""
    ev = ChannelEvent(provider=inbound.provider, provider_event_id=inbound.event_id,
                      organization_id=hotel.organization_id, hotel_id=hotel.id,
                      status="processing")
    db.add(ev)
    try:
        db.commit()
        return ev, "new"
    except IntegrityError:
        db.rollback()
    existing = db.scalar(select(ChannelEvent).where(
        ChannelEvent.provider == inbound.provider,
        ChannelEvent.provider_event_id == inbound.event_id).with_for_update())
    if existing is None:
        return None, "duplicate"
    age = datetime.now(UTC) - existing.updated_at
    if existing.status == "failed" or (existing.status == "processing" and age > STALE_AFTER):
        existing.status, existing.error = "processing", None
        db.commit()
        return existing, "reclaimed"
    db.rollback()
    return None, "duplicate"


def process_inbound(db: Session, *, settings: Settings, hotel: Hotel, inbound: InboundMessage,
                    senders: delivery.SenderFactory, llm: LLMProvider | None,
                    embedder: EmbeddingProvider, store: VectorStore) -> dict:
    """Handle one inbound delivery exactly once and attempt to send the reply.

    Idempotency layers: (provider, event id) uniqueness here, the message-level
    client_message_id in the orchestrator, and per-part uniqueness on outbound rows. A retried
    webhook therefore never repeats an action or a reply."""
    ev, state = _claim(db, inbound, hotel)
    if ev is None:
        metrics.WEBHOOKS.labels(inbound.provider, "duplicate").inc()
        return {"status": "duplicate"}
    channel = CHANNEL_FOR_PROVIDER[inbound.provider]
    try:
        conv = convs.open_conversation(db, settings=settings, hotel=hotel, channel=channel,
                                       external_id=inbound.sender)
        ev.conversation_id = conv.id
        client_id = f"{inbound.provider}:{inbound.event_id}"
        if inbound.text is None:
            conv = convs.lock_conversation(db, conv.id)
            prior = convs.find_reply(db, conv.id, client_id)
            if prior is None:
                convs.add_message(db, conv, role="user", content="[unsupported attachment]",
                                  external_message_id=client_id)
                prior = convs.add_message(db, conv, role="assistant", content=UNSUPPORTED_REPLY,
                                          meta={"reply_to": client_id, "outcome": "needs_info"})
                db.commit()
            msg = prior
        else:
            _, msg = handle_message(
                db, settings=settings, conversation_id=conv.id, text=inbound.text,
                client_message_id=client_id, actor="guest", hotel=hotel, llm=llm,
                embedder=embedder, store=store)
        sender = senders(channel)
        rows = delivery.enqueue(db, db.get(type(conv), conv.id), msg,
                                sender.max_chars if sender else 1500)
        db.commit()
        for row in rows:
            delivery.attempt(db, row, sender, settings)
        ev = db.get(ChannelEvent, ev.id)
        ev.status, ev.conversation_id = "processed", conv.id
        db.commit()
        metrics.WEBHOOKS.labels(inbound.provider, "processed").inc()
        return {"status": "processed", "reclaimed": state == "reclaimed"}
    except Exception as exc:
        db.rollback()
        ev = db.get(ChannelEvent, ev.id)
        if ev is not None:
            ev.status, ev.error = "failed", type(exc).__name__
            db.commit()
        metrics.WEBHOOKS.labels(inbound.provider, "failed").inc()
        log.exception("inbound %s failed", inbound.event_id)
        raise
