import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.agents.context import AgentContext, Reply
from app.agents.graph import GRAPH
from app.config import Settings
from app.conversations import service as convs
from app.conversations.redact import redact_pii
from app.conversations.state import ConversationState
from app.llm.base import LLMProvider
from app.models import AgentExecution, Conversation, Hotel, Message, User
from app.rag.embeddings import EmbeddingProvider
from app.rag.vectorstore import VectorStore
from app.services.errors import ConflictError, ValidationFailed

log = logging.getLogger("hospitality.agent")


DUPLICATE_WAIT_SECONDS = 20.0


def _stored_reply(m: Message) -> Reply:
    return Reply(m.content, outcome=m.meta.get("outcome", "answered"),
                 citations=m.meta.get("citations", []),
                 awaiting_confirmation=m.meta.get("awaiting_confirmation", False),
                 ticket_numbers=m.meta.get("ticket_numbers", []),
                 reservation_codes=m.meta.get("reservation_codes", []))


def handle_message(
    db: Session, *, settings: Settings, conversation_id: uuid.UUID, text: str,
    client_message_id: str, actor: str, hotel: Hotel, llm: LLMProvider | None,
    embedder: EmbeddingProvider, store: VectorStore, user: User | None = None,
) -> tuple[Reply, Message]:
    """Process one inbound message exactly once and return the assistant reply.

    The conversation row is locked for the duration of the first statements so concurrent
    duplicates serialise; a repeated client_message_id returns the stored reply and performs no
    actions (webhook retries, double submits)."""
    text = text.strip()
    if not text:
        raise ValidationFailed("Message is empty")
    if len(text) > settings.max_message_chars:
        raise ValidationFailed(f"Message is longer than {settings.max_message_chars} characters")
    if not client_message_id or len(client_message_id) > 128:
        raise ValidationFailed("client_message_id is required (max 128 characters)")

    conv = convs.lock_conversation(db, conversation_id)
    prior = convs.find_reply(db, conv.id, client_message_id)
    if prior is not None:
        return _stored_reply(prior), prior

    started = time.monotonic()
    state = ConversationState.load(conv.state)
    execution = AgentExecution(organization_id=conv.organization_id, hotel_id=conv.hotel_id,
                               conversation_id=conv.id, correlation_id=uuid.uuid4().hex)
    db.add(execution)
    try:
        convs.add_message(db, conv, role="user", content=text,
                          external_message_id=client_message_id)
    except ConflictError:
        # The same delivery is already being processed (webhook retry / double submit):
        # wait for the first run's reply instead of acting again.
        deadline = time.monotonic() + DUPLICATE_WAIT_SECONDS
        while time.monotonic() < deadline:
            db.rollback()
            prior = convs.find_reply(db, conversation_id, client_message_id)
            if prior is not None:
                return _stored_reply(prior), prior
            time.sleep(0.2)
        raise
    db.commit()  # the inbound message is durable before any action runs

    ctx = AgentContext(db=db, settings=settings, hotel=hotel, conv=conv, state=state,
                       actor=actor, user=user, llm=llm, embedder=embedder, store=store,
                       execution=execution, correlation_id=execution.correlation_id)
    try:
        out = GRAPH.invoke({"text": text}, config={
            "configurable": {"ctx": ctx}, "recursion_limit": settings.agent_max_steps + 8})
        reply: Reply = out["reply"]
    except Exception:
        log.exception("agent graph failed")
        db.rollback()
        reply = Reply("I'm sorry, something went wrong on my side. Please try again or contact "
                      "the front desk.", outcome="error")

    conv = db.get(Conversation, conv.id)
    conv.state = ctx.state.dump()
    if ctx.conv.status != conv.status:
        conv.status = ctx.conv.status
    meta = {"reply_to": client_message_id, "outcome": reply.outcome,
            "intent": ctx.state.last_intent, "citations": reply.citations,
            "awaiting_confirmation": reply.awaiting_confirmation,
            "ticket_numbers": reply.ticket_numbers, "reservation_codes": reply.reservation_codes,
            "correlation_id": ctx.correlation_id}
    msg = convs.add_message(db, conv, role="assistant", content=reply.text, meta=meta)
    execution = db.get(AgentExecution, execution.id)
    execution.outcome, execution.intent = reply.outcome, ctx.state.last_intent
    execution.steps = ctx.steps
    execution.latency_ms = int((time.monotonic() - started) * 1000)
    execution.tokens_in, execution.tokens_out = ctx.tokens_in, ctx.tokens_out
    execution.trace = [{**t, "info": redact_pii(t["info"])} for t in ctx.trace]
    db.commit()
    try:
        convs.maybe_summarize(db, conv, settings, llm)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("summarisation failed")
    return reply, msg
