from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.channels.base import SendResult
from app.models import (
    ChannelEvent, Conversation, IngestionJob, KnowledgeDocument, OutboundMessage,
)
from worker import main as worker_main
from worker import tasks


def test_worker_delivers_due_outbound_and_skips_future(channels, db):
    channels.wa.results = [SendResult(False, retryable=True, error="HTTP 500")]
    channels.wa_send("What time is check-out?")
    ob = db.scalar(select(OutboundMessage))
    assert ob.status == "pending"
    from app.api.routes.webhooks import get_senders  # worker builds real senders from settings
    assert tasks.deliver_outbound(db, channels.settings.model_copy(
        update={"twilio_auth_token": ""})) == 0          # not due yet
    ob.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    # With credentials absent the worker cannot send: the row stays pending, not "sent".
    tasks.deliver_outbound(db, channels.settings.model_copy(update={"twilio_account_sid": ""}))
    db.refresh(ob)
    assert ob.status == "pending" and ob.attempts == 2 and ob.error == "channel not configured"
    assert get_senders and channels.wa.sent == []


def test_worker_purges_expired_conversations(chat, db):
    c = chat()
    c.say("hello")
    conv = db.get(Conversation, c.conversation_id)
    conv.retention_expires_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    assert tasks.purge_conversations(db, None) == 1
    assert db.get(Conversation, c.conversation_id) is None


def test_worker_reaps_stuck_ingestions_and_events(seeded, db):
    from app.models import Hotel
    from scripts.seed import uid
    hotel = db.get(Hotel, uid("hotel"))
    old = datetime.now(UTC) - timedelta(hours=2)
    doc = KnowledgeDocument(organization_id=hotel.organization_id, hotel_id=hotel.id,
                            title="Half done", doc_type="policy", doc_key="half", version=1,
                            status="processing", is_active=False)
    db.add(doc)
    db.flush()
    db.add(IngestionJob(organization_id=hotel.organization_id, hotel_id=hotel.id,
                        document_id=doc.id, status="running"))
    ev = ChannelEvent(provider="telegram", provider_event_id="stuck-1", status="processing")
    fresh = ChannelEvent(provider="telegram", provider_event_id="fresh-1", status="processing")
    db.add_all([ev, fresh])
    db.commit()
    db.execute(KnowledgeDocument.__table__.update().where(KnowledgeDocument.id == doc.id)
               .values(updated_at=old))
    db.execute(ChannelEvent.__table__.update().where(ChannelEvent.id == ev.id)
               .values(updated_at=old))
    db.commit()
    assert tasks.reap_stuck_ingestions(db, None) == 1
    assert tasks.reap_stuck_channel_events(db, None) == 1
    db.expire_all()
    assert db.get(KnowledgeDocument, doc.id).status == "failed"
    job = db.scalar(select(IngestionJob).where(IngestionJob.document_id == doc.id))
    assert job.status == "failed" and "worker timeout" in job.error
    assert db.get(ChannelEvent, ev.id).status == "failed"
    assert db.get(ChannelEvent, fresh.id).status == "processing"   # recent work is left alone


def test_run_once_isolates_task_failures(seeded, monkeypatch):
    def boom(db, settings):
        raise RuntimeError("task exploded")

    monkeypatch.setitem(tasks.TASKS, "reap_stuck_ingestions", (boom, 1))
    monkeypatch.setitem(worker_main.TASKS, "reap_stuck_ingestions", (boom, 1))
    res = worker_main.run_once()
    assert res["reap_stuck_ingestions"] == -1 and res["purge_conversations"] >= 0
    assert set(res) == set(tasks.TASKS)
    assert set(worker_main.run_once({"purge_conversations"})) == {"purge_conversations"}
