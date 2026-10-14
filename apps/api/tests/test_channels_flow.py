import threading
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.channels import delivery
from app.channels.base import SendResult
from app.channels.inbox import UNSUPPORTED_REPLY
from app.models import (
    ChannelEvent, Conversation, Notification, OutboundMessage, Reservation, ServiceRequest,
)
from tests.conftest import DEMO_PASSWORD, HOTEL_ID, WA_NUMBER

BASE = f"/api/v1/hotels/{HOTEL_ID}"


# ----------------------------------------------------------------- WhatsApp / Twilio

def test_unsigned_or_badly_signed_requests_are_rejected_without_side_effects(channels, db):
    assert channels.wa_send("hello", sign=False).status_code == 403
    bad = channels.client.post("/api/v1/webhooks/twilio/whatsapp",
                               data={"MessageSid": "SM" + "1" * 32, "From": "whatsapp:+1415",
                                     "To": WA_NUMBER, "Body": "x"},
                               headers={"X-Twilio-Signature": "forged"})
    assert bad.status_code == 403
    assert db.query(ChannelEvent).count() == 0 and db.query(Conversation).count() == 0
    assert channels.wa.sent == []


def test_webhook_fails_closed_when_unconfigured(channels, use_settings):
    use_settings(twilio_auth_token="")
    assert channels.wa_send("hello").status_code == 503
    use_settings(twilio_auth_token="tok-secret", public_base_url="")
    assert channels.wa_send("hello").status_code == 503


def test_validation_can_only_be_disabled_explicitly(channels, use_settings):
    use_settings(twilio_webhook_validate=False)  # local development only
    assert channels.wa_send("What time is check-out?", sign=False).status_code == 200


def test_payload_validation_and_routing(channels, db):
    assert channels.wa_send("hi", sid="not-a-sid").status_code == 400
    assert channels.wa_send("hi", frm="+14155551234").status_code == 400
    assert channels.wa_send("hi", to="whatsapp:+19998887777").status_code == 404  # unknown number
    assert db.query(ChannelEvent).count() == 0


def test_policy_question_over_whatsapp_is_answered_and_delivered(channels, db):
    r = channels.wa_send("What time is check-out?", frm="whatsapp:+14155551234")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/xml")
    to, body = channels.wa.sent[0]
    assert to == "whatsapp:+14155551234" and "11:00 AM" in body
    conv = db.scalar(select(Conversation).where(Conversation.channel == "whatsapp"))
    assert conv is not None
    ob = db.scalar(select(OutboundMessage))
    assert ob.status == "sent" and ob.attempts == 1 and ob.provider_message_id == "mock-1"
    ev = db.scalar(select(ChannelEvent))
    assert ev.status == "processed" and ev.conversation_id == conv.id


def test_duplicate_deliveries_are_processed_once(channels, db):
    sid = "SM" + "d" * 32
    for _ in range(3):
        assert channels.wa_send("What time is check-out?", sid=sid).status_code == 200
    assert len(channels.wa.sent) == 1
    assert db.query(OutboundMessage).count() == 1 and db.query(ChannelEvent).count() == 1


def test_concurrent_duplicate_deliveries_are_processed_once(channels, db):
    sid, out = "SM" + "e" * 32, []

    def go():
        out.append(channels.wa_send("What time is check-out?", sid=sid).status_code)

    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == [200] * 5 and len(channels.wa.sent) == 1


def test_media_without_text_gets_graceful_reply_and_caption_text_is_used(channels, db):
    channels.wa_send("", media=1)
    assert channels.wa.sent[-1][1] == UNSUPPORTED_REPLY
    channels.wa_send("What time is check-out?", media=1, frm="whatsapp:+14155559999")
    assert "11:00 AM" in channels.wa.sent[-1][1]


def test_guest_can_complete_a_reservation_entirely_over_whatsapp(channels, db):
    ci = date.today() + timedelta(days=30)
    co = ci + timedelta(days=2)
    me = "whatsapp:+14155550777"
    for text in ("I'd like to book a room", f"{ci} to {co} for 2 adults", "Deluxe King",
                 "Jane Doe"):
        channels.wa_send(text, frm=me)
    assert db.query(Reservation).count() == 0
    channels.wa_send("yes", frm=me)
    res = db.scalar(select(Reservation))
    assert res and res.confirmation_code in channels.wa.sent[-1][1]
    # a different phone number is a different guest with its own (empty) conversation
    channels.wa_send("yes", frm="whatsapp:+14155550888")
    assert db.query(Reservation).count() == 1


def test_long_replies_are_split_into_parts(channels, db):
    from app.agents import orchestrator
    long_reply = ("Here is a long answer. " * 120).strip()
    orig = orchestrator.GRAPH.invoke
    from app.agents import graph
    try:
        orchestrator.GRAPH.invoke = lambda *a, **k: {"reply": graph.Reply(long_reply)}
        channels.wa_send("What time is check-out?")
    finally:
        orchestrator.GRAPH.invoke = orig
    parts = [b for _, b in channels.wa.sent]
    assert len(parts) >= 2 and all(len(p) <= 1500 for p in parts)
    assert db.query(OutboundMessage).count() == len(parts)


# ----------------------------------------------------------------- delivery failures & retries

def test_retryable_failure_is_scheduled_and_recovered_by_the_retry_loop(channels, db, use_settings):
    channels.wa.results = [SendResult(False, retryable=True, error="HTTP 503")]
    assert channels.wa_send("What time is check-out?").status_code == 200  # webhook still acks
    ob = db.scalar(select(OutboundMessage))
    assert ob.status == "pending" and ob.attempts == 1 and ob.error == "HTTP 503"
    assert ob.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)
    assert delivery.deliver_due(db, lambda ch: channels.wa, channels.settings) == 0  # not due yet
    ob.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert delivery.deliver_due(db, lambda ch: channels.wa, channels.settings) == 1
    db.refresh(ob)
    assert ob.status == "sent" and ob.attempts == 2 and len(channels.wa.sent) == 1


def test_rate_limit_retry_after_is_honoured(channels, db):
    channels.wa.results = [SendResult(False, retryable=True, error="HTTP 429", retry_after=600)]
    channels.wa_send("What time is check-out?")
    ob = db.scalar(select(OutboundMessage))
    assert ob.next_attempt_at > datetime.now(UTC) + timedelta(seconds=590)


def test_non_retryable_failure_is_final(channels, db):
    channels.wa.results = [SendResult(False, retryable=False, error="HTTP 400 code 21211")]
    channels.wa_send("What time is check-out?")
    ob = db.scalar(select(OutboundMessage))
    assert ob.status == "failed" and "21211" in ob.error and ob.attempts == 1


def test_retries_stop_after_max_attempts(channels, db):
    channels.wa.results = [SendResult(False, retryable=True, error="HTTP 500")] * 10
    channels.wa_send("What time is check-out?")
    for _ in range(10):
        ob = db.scalar(select(OutboundMessage))
        db.refresh(ob)
        if ob.status != "pending":
            break
        ob.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        delivery.deliver_due(db, lambda ch: channels.wa, channels.settings)
    db.refresh(ob)
    assert ob.status == "dead" and ob.attempts == channels.settings.outbound_max_attempts


def test_missing_credentials_leave_the_reply_pending_instead_of_pretending(channels, db):
    from app.main import app
    from app.api.routes.webhooks import get_senders
    app.dependency_overrides[get_senders] = lambda: (lambda ch: None)
    channels.wa_send("What time is check-out?")
    ob = db.scalar(select(OutboundMessage))
    assert ob.status == "pending" and ob.error == "channel not configured"
    assert channels.wa.sent == []


def test_concurrent_workers_do_not_double_send(channels, db):
    channels.wa.results = [SendResult(False, retryable=True, error="HTTP 500")]
    channels.wa_send("What time is check-out?")
    ob = db.scalar(select(OutboundMessage))
    ob.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    from app.db.session import SessionLocal
    counts = []

    def worker():
        with SessionLocal() as s:
            counts.append(delivery.deliver_due(s, lambda ch: channels.wa, channels.settings))

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(counts) == 1 and len(channels.wa.sent) == 1


# ----------------------------------------------------------------- Telegram

def test_telegram_secret_token_is_required(channels, db):
    assert channels.tg_send("hi", secret=None).status_code == 401
    assert channels.tg_send("hi", secret="wrong").status_code == 401
    assert db.query(ChannelEvent).count() == 0 and channels.tg.sent == []


def test_telegram_round_trip_and_idempotency(channels, db):
    assert channels.tg_send("What time is check-out?", chat=555, update_id=9001).status_code == 200
    assert channels.tg.sent[0][0] == "555" and "11:00 AM" in channels.tg.sent[0][1]
    again = channels.tg_send("What time is check-out?", chat=555, update_id=9001)
    assert again.json()["status"] == "duplicate" and len(channels.tg.sent) == 1


def test_telegram_unknown_integration_ignored_updates_and_bad_payloads(channels, db):
    r = channels.client.post(f"/api/v1/webhooks/telegram/{HOTEL_ID}", json={"update_id": 1},
                             headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert r.status_code == 404
    edited = channels.tg_send(raw={"update_id": 2, "edited_message": {"message_id": 1}})
    assert edited.json() == {"status": "ignored"}
    assert channels.tg_send(raw={"update_id": "x"}).status_code == 400
    r = channels.client.post(f"/api/v1/webhooks/telegram/{channels.tg_id}", content=b"not json",
                             headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert r.status_code == 400
    assert channels.tg.sent == []


def test_telegram_media_start_and_oversized(channels, db):
    channels.tg_send(None, extra={"photo": [{"file_id": "f"}]})
    assert channels.tg.sent[-1][1] == UNSUPPORTED_REPLY
    channels.tg_send("/start", chat=888)
    assert "welcome" in channels.tg.sent[-1][1].lower()
    big = channels.client.post(f"/api/v1/webhooks/telegram/{channels.tg_id}", content=b"x" * 70000,
                               headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret",
                                        "content-type": "application/json"})
    assert big.status_code == 413


def test_processing_crash_returns_5xx_and_is_safely_retried(channels, db, monkeypatch):
    from app.channels import inbox
    real = inbox.handle_message
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return real(*a, **kw)

    monkeypatch.setattr(inbox, "handle_message", flaky)
    from fastapi.testclient import TestClient

    from app.main import app
    lenient = TestClient(app, raise_server_exceptions=False)  # sees the 500 a provider would
    first = lenient.post(
        f"/api/v1/webhooks/telegram/{channels.tg_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"},
        json={"update_id": 4242, "message": {"message_id": 1, "chat": {"id": 777, "type": "private"},
                                             "text": "What time is check-out?"}})
    assert first.status_code == 500 and "hiccup" not in first.text
    ev = db.scalar(select(ChannelEvent).where(ChannelEvent.provider_event_id == "4242"))
    assert ev.status == "failed"
    retry = channels.tg_send("What time is check-out?", update_id=4242)  # Telegram redelivers
    assert retry.status_code == 200 and len(channels.tg.sent) == 1
    db.refresh(ev)
    assert ev.status == "processed"


def test_emergency_over_telegram_creates_ticket_and_critical_notification(channels, db, client,
                                                                          auth):
    channels.tg_send("There is smoke coming from the vent in my room", chat=321)
    assert "emergency number" in channels.tg.sent[-1][1]
    t = db.scalar(select(ServiceRequest).where(ServiceRequest.category == "emergency"))
    assert t and t.priority.value == "urgent"
    n = db.scalar(select(Notification).where(Notification.kind == "ticket_emergency"))
    assert n.severity == "critical" and t.ticket_number in n.title
    assert "smoke" not in n.title  # notifications carry references, not guest text
    rec = client.get(f"{BASE}/notifications", headers=auth("reception@demo.hotel")).json()
    assert [x["kind"] for x in rec] == ["ticket_emergency"] and rec[0]["read"] is False
    assert client.get(f"{BASE}/notifications", headers=auth("housekeeping@demo.hotel")).json() == []
    nid = rec[0]["id"]
    assert client.post(f"{BASE}/notifications/{nid}/read",
                       headers=auth("reception@demo.hotel")).status_code == 204
    assert client.get(f"{BASE}/notifications?unread=true",
                      headers=auth("reception@demo.hotel")).json() == []
    # another user still sees it as unread; dept staff cannot mark what they cannot see
    assert client.get(f"{BASE}/notifications?unread=true",
                      headers=auth("manager@demo.hotel")).json()[0]["id"] == nid
    assert client.post(f"{BASE}/notifications/{nid}/read",
                       headers=auth("housekeeping@demo.hotel")).status_code == 404


def test_handoff_then_staff_reply_is_delivered_on_the_guests_channel(channels, db, client, auth):
    channels.tg_send("I want to speak to a manager", chat=654)
    conv = db.scalar(select(Conversation).where(Conversation.channel == "telegram"))
    assert conv.status == "handed_off"
    r = client.post(f"{BASE}/conversations/{conv.id}/staff-messages",
                    headers=auth("reception@demo.hotel"), json={"text": "Hi, Riley here."})
    assert r.status_code == 201
    assert channels.tg.sent[-1] == ("654", "Hi, Riley here.")
    ob = db.scalars(select(OutboundMessage).order_by(OutboundMessage.created_at)).all()[-1]
    assert ob.status == "sent" and ob.channel == "telegram"


def test_staff_reply_on_web_conversation_is_not_pushed_to_a_channel(channels, db, client, auth, chat):
    c = chat()
    c.say("I want to speak to a manager")
    client.post(f"{BASE}/conversations/{c.conversation_id}/staff-messages",
                headers=auth("reception@demo.hotel"), json={"text": "Hello"})
    assert db.query(OutboundMessage).count() == 0


def test_approval_request_notifies_staff(channels, db, client, auth, in_house, chat):
    code, _ = in_house
    c = chat()
    c.verify(code, "Doe")
    c.say("late checkout please")
    kinds = [n["kind"] for n in client.get(f"{BASE}/notifications",
                                           headers=auth("manager@demo.hotel")).json()]
    assert "approval_requested" in kinds


# ----------------------------------------------------------------- tenancy and integration admin

def test_numbers_route_to_the_right_tenant_and_cannot_leak_across(channels, db, client, other_org):
    from app.models import IntegrationConfig
    other_number = "whatsapp:+14155550200"
    db.add(IntegrationConfig(organization_id=other_org["org_id"], hotel_id=other_org["hotel_id"],
                             provider="twilio_whatsapp", enabled=True,
                             config={"number": other_number}))
    db.commit()
    channels.wa_send("What time is check-out?", to=other_number, frm="whatsapp:+14155551111")
    reply = channels.wa.sent[-1][1]
    assert "11:00 AM" not in reply and "don't have verified information" in reply
    conv = db.scalar(select(Conversation).where(Conversation.hotel_id == other_org["hotel_id"]))
    assert conv is not None
    # the same phone number writing to each hotel gets two separate guests/conversations
    channels.wa_send("hello", frm="whatsapp:+14155551111")
    convs = db.scalars(select(Conversation).where(Conversation.channel == "whatsapp")).all()
    assert len({c.hotel_id for c in convs}) == 2 and len({c.guest_id for c in convs}) == 2


def test_integration_admin_rules_and_honest_status(channels, client, auth, db, other_org):
    admin, mgr = auth("admin@demo.hotel"), auth("manager@demo.hotel")
    rows = {r["provider"]: r for r in client.get(f"{BASE}/integrations", headers=mgr).json()}
    assert rows["twilio_whatsapp"]["status"] == "configured (live delivery not verified)"
    assert rows["twilio_whatsapp"]["webhook_url"] == "https://hotel.example/api/v1/webhooks/twilio/whatsapp"
    assert rows["telegram"]["webhook_url"].endswith(f"/telegram/{rows['telegram']['id']}")
    assert "token" not in str(rows).lower().replace("secret_token", "")
    assert client.put(f"{BASE}/integrations/twilio_whatsapp", headers=mgr,
                      json={"enabled": True, "config": {"number": WA_NUMBER}}).status_code == 403
    assert client.put(f"{BASE}/integrations/twilio_whatsapp", headers=admin,
                      json={"enabled": True, "config": {"number": "12345"}}).status_code == 422
    assert client.put(f"{BASE}/integrations/sms", headers=admin,
                      json={"enabled": True}).status_code == 404
    off = client.put(f"{BASE}/integrations/twilio_whatsapp", headers=admin,
                     json={"enabled": False, "config": {}}).json()
    assert off["status"] == "not configured"
    assert channels.wa_send("hello").status_code == 404  # disabled integration stops routing
    # number uniqueness across hotels
    tok = client.post("/api/v1/auth/login", json={"email": other_org["email"],
                                                  "password": DEMO_PASSWORD}).json()["access_token"]
    other_admin = {"Authorization": f"Bearer {tok}"}
    r = client.put(f"/api/v1/hotels/{other_org['hotel_id']}/integrations/twilio_whatsapp",
                   headers=other_admin, json={"enabled": True, "config": {"number": "whatsapp:+14155550300"}})
    assert r.status_code == 403  # manager of the other org is not an admin
    assert client.get(f"{BASE}/integrations", headers=other_admin).status_code == 404


def test_production_settings_guards():
    import pytest
    from app.config import Settings
    base = dict(environment="production", app_secret_key="x" * 48, vector_store_provider="pinecone",
                pinecone_api_key="k", embedding_provider="sentence-transformers",
                metrics_token="m" * 24)
    Settings(**base).validate_for_runtime()
    with pytest.raises(RuntimeError, match="TWILIO_WEBHOOK_VALIDATE"):
        Settings(**base, twilio_webhook_validate=False).validate_for_runtime()
    with pytest.raises(RuntimeError, match="TELEGRAM_WEBHOOK_SECRET"):
        Settings(**base, telegram_bot_token="1:x").validate_for_runtime()
