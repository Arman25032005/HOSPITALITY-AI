import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.base import Sender, chunk_text
from app.config import Settings
from app.core import metrics
from app.models import Conversation, GuestChannelIdentity, Message, OutboundMessage

log = logging.getLogger("hospitality.channels")
SenderFactory = Callable[[str], Sender | None]


def backoff_seconds(attempts: int) -> int:
    return min(3600, 30 * 2 ** max(0, attempts - 1))


def recipient_for(db: Session, conv: Conversation) -> str | None:
    return db.scalar(select(GuestChannelIdentity.external_id).where(
        GuestChannelIdentity.organization_id == conv.organization_id,
        GuestChannelIdentity.guest_id == conv.guest_id,
        GuestChannelIdentity.channel == conv.channel))


def enqueue(db: Session, conv: Conversation, message: Message, max_chars: int) -> list[OutboundMessage]:
    """One outbound row per message part. Unique (message_id, part) makes enqueueing idempotent."""
    recipient = recipient_for(db, conv)
    if recipient is None:
        return []
    existing = {o.part: o for o in db.scalars(select(OutboundMessage).where(
        OutboundMessage.message_id == message.id))}
    out = []
    for i, part in enumerate(chunk_text(message.content, max_chars)):
        row = existing.get(i)
        if row is None:
            row = OutboundMessage(
                organization_id=conv.organization_id, hotel_id=conv.hotel_id,
                conversation_id=conv.id, message_id=message.id, part=i, channel=conv.channel,
                recipient=recipient, body=part, status="pending")
            db.add(row)
        out.append(row)
    db.flush()
    return out


def attempt(db: Session, row: OutboundMessage, sender: Sender | None, settings: Settings) -> str:
    """Try to deliver one row and record the outcome. Returns the new status."""
    if row.status not in ("pending",):
        return row.status
    row.attempts += 1
    if sender is None:
        res_ok, retryable, err, retry_after, pid = False, True, "channel not configured", None, None
    else:
        res = sender.send(row.recipient, row.body)
        res_ok, retryable, err, retry_after, pid = (res.ok, res.retryable, res.error,
                                                    res.retry_after, res.provider_message_id)
    if res_ok:
        row.status, row.provider_message_id, row.error = "sent", pid, None
    elif retryable and row.attempts < settings.outbound_max_attempts:
        delay = max(backoff_seconds(row.attempts), int(retry_after or 0))
        row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
        row.error = err
    else:
        row.status = "dead" if retryable else "failed"
        row.error = err
        log.warning("outbound %s gave up after %s attempts: %s", row.id, row.attempts, err)
    db.commit()
    metrics.OUTBOUND.labels(row.channel, row.status if row.status != "pending" else "retry").inc()
    return row.status


def deliver_due(db: Session, senders: SenderFactory, settings: Settings, limit: int = 50) -> int:
    """Claim and attempt due rows. SKIP LOCKED lets several workers run safely."""
    rows = db.scalars(select(OutboundMessage).where(
        OutboundMessage.status == "pending",
        OutboundMessage.next_attempt_at <= datetime.now(UTC))
        .order_by(OutboundMessage.next_attempt_at).limit(limit)
        .with_for_update(skip_locked=True)).all()
    for row in rows:
        attempt(db, row, senders(row.channel), settings)
    return len(rows)
