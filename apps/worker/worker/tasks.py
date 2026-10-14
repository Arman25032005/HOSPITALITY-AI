"""Background tasks. Each takes a Session and returns a count, so they are trivially testable.

The queue is the database itself (outbound_messages rows with next_attempt_at, retention dates),
claimed with FOR UPDATE SKIP LOCKED, so any number of workers can run without a broker."""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import delivery
from app.channels.registry import build_sender
from app.config import Settings
from app.conversations import service as convs
from app.models import ChannelEvent, IngestionJob, KnowledgeDocument

log = logging.getLogger("hospitality.worker")


def deliver_outbound(db: Session, settings: Settings) -> int:
    return delivery.deliver_due(db, lambda ch: build_sender(settings, ch), settings)


def purge_conversations(db: Session, settings: Settings) -> int:
    return convs.purge_expired(db)


def reap_stuck_ingestions(db: Session, settings: Settings, older_than_minutes: int = 15) -> int:
    """Documents left in 'processing' (process died mid-ingestion) are marked failed so staff
    can see and retry them; they were never active, so retrieval is unaffected."""
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    docs = db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.status == "processing", KnowledgeDocument.updated_at < cutoff)).all()
    for d in docs:
        d.status, d.is_active = "failed", False
        for job in db.scalars(select(IngestionJob).where(IngestionJob.document_id == d.id,
                                                         IngestionJob.status == "running")):
            job.status, job.error = "failed", "worker timeout: ingestion did not finish"
    db.commit()
    return len(docs)


def reap_stuck_channel_events(db: Session, settings: Settings, older_than_minutes: int = 10) -> int:
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    evs = db.scalars(select(ChannelEvent).where(ChannelEvent.status == "processing",
                                                ChannelEvent.updated_at < cutoff)).all()
    for ev in evs:
        ev.status, ev.error = "failed", "timed out"  # provider redelivery can reclaim it
    db.commit()
    return len(evs)


TASKS = {
    "deliver_outbound": (deliver_outbound, 10),          # (function, interval seconds)
    "reap_stuck_ingestions": (reap_stuck_ingestions, 300),
    "reap_stuck_channel_events": (reap_stuck_channel_events, 300),
    "purge_conversations": (purge_conversations, 3600),
}
