import threading
from datetime import date, timedelta

from sqlalchemy import select

from app.models import (
    AgentExecution, ApprovalRequest, AuditEvent, Conversation, Message, ServiceRequest,
    ToolExecution, User,
)
from tests.conftest import DEMO_PASSWORD, HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}"


def verified_chat(chat, in_house):
    code, _ = in_house
    c = chat()
    assert c.verify(code, "Doe")["verified"] is True
    return c, code


# -------------------------------------------------------- workflow 3: maintenance

def test_maintenance_requires_verified_stay(chat, db):
    r = chat().say("My AC is broken")
    assert r["outcome"] == "needs_info" and "verify" in r["reply"].lower()
    assert db.query(ServiceRequest).count() == 0


def test_verification_is_attempt_limited_and_uniform(chat, in_house):
    code, _ = in_house
    c = chat()
    assert c.verify(code, "Wrong") == {"verified": False, "attempts_left": 4}
    assert c.verify("R-AAAAAAAA", "Doe")["verified"] is False  # unknown code, same answer
    for _ in range(3):
        c.verify(code, "Nope")
    locked = c.client.post("/api/v1/chat/verify", headers=c.headers, json={
        "confirmation_code": code, "last_name": "Doe"})
    assert locked.status_code == 403  # even the right name is refused after 5 failures


def test_ac_request_creates_real_ticket_tracked_by_status(chat, in_house, db, client, auth):
    c, code = verified_chat(chat, in_house)
    r = c.say("The AC in my room is broken and it is very hot")
    assert r["outcome"] == "action_done" and len(r["ticket_numbers"]) == 1
    number = r["ticket_numbers"][0]
    db.expire_all()
    t = db.scalar(select(ServiceRequest).where(ServiceRequest.ticket_number == number))
    assert t and t.category == "maintenance" and t.status.value == "open"
    assert t.room_number is not None and t.department_id is not None  # room from check-in
    assert t.reservation_id is not None and t.guest_id is not None
    assert number in r["reply"] and "maintenance" in r["reply"]
    assert "will arrive" not in r["reply"] and "minutes" not in r["reply"]  # no invented ETA

    assert "open" in c.say("What is the status of my request?")["reply"]
    # staff progress the ticket; the guest sees the stored state, not a guess
    mh = auth("maintenance@demo.hotel")
    assert client.post(f"{BASE}/tickets/{t.id}/status", json={"status": "in_progress"},
                       headers=mh).status_code == 200
    assert "in progress" in c.say("any update on my request?")["reply"]


def test_repeating_a_request_does_not_create_duplicate_tickets(chat, in_house, db):
    c, _ = verified_chat(chat, in_house)
    c.say("my TV is not working")
    r = c.say("my TV is not working!!")
    assert "already have an open" in r["reply"]
    assert db.query(ServiceRequest).count() == 1


def test_duplicate_delivery_creates_single_ticket(chat, in_house, db, client):
    c, _ = verified_chat(chat, in_house)
    out = []

    def go():
        r = client.post("/api/v1/chat/messages", headers=c.headers, json={
            "text": "Please send extra towels", "client_message_id": "towels-1"})
        out.append(r.status_code)

    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out == [200, 200, 200] and db.query(ServiceRequest).count() == 1


def test_housekeeping_and_priority_routing(chat, in_house, db):
    c, _ = verified_chat(chat, in_house)
    c.say("I need extra towels and pillows please")
    c.say("There is a leak in my bathroom, water everywhere")
    cats = {t.category: t.priority.value for t in db.scalars(select(ServiceRequest))}
    assert cats == {"housekeeping": "normal", "maintenance": "high"}


def test_other_guest_cannot_see_or_use_someone_elses_stay(chat, in_house, db):
    a, code = verified_chat(chat, in_house)
    ticket = a.say("my shower is broken")["ticket_numbers"][0]
    b = chat()
    assert "verify" in b.say("my toilet is broken")["reply"].lower()
    peek = b.say(f"what is the status of ticket {ticket}?")
    assert ticket not in peek["reply"] and "can't find" in peek["reply"]
    probe = b.say(f"status of reservation {code}")["reply"]
    assert "stay" not in probe.lower().replace("verifying it", "") or "verify" in probe.lower()
    assert "checked_in" not in probe and "adult" not in probe  # no reservation details leaked
    assert "Doe" not in "".join(m["content"] for m in b.history())


def test_future_stay_cannot_raise_room_requests(chat, client, auth, db):
    rec = auth("reception@demo.hotel")
    g = client.post(f"{BASE}/guests", json={"full_name": "Sam Future"}, headers=rec).json()
    ci = date.today() + timedelta(days=15)
    from scripts.seed import uid
    res = client.post(f"{BASE}/reservations", headers=rec, json={
        "check_in": ci.isoformat(), "check_out": (ci + timedelta(days=2)).isoformat(),
        "adults": 1, "guest_id": g["id"], "room_type_id": str(uid("rt-standard"))}).json()
    c = chat()
    assert c.verify(res["confirmation_code"], "Future")["verified"]
    r = c.say("my AC is broken")
    assert "current stay" in r["reply"] and db.query(ServiceRequest).count() == 0


# -------------------------------------------------------- workflow 4: late checkout

def test_late_checkout_unverified_gets_policy_and_no_request(chat, db):
    r = chat().say("Can I get a late checkout?")
    assert "Our late checkout policy" in r["reply"] and "availability" in r["reply"]
    assert "verify" in r["reply"].lower() and db.query(ApprovalRequest).count() == 0


def test_late_checkout_needs_staff_approval_and_reports_only_verified_result(
        chat, in_house, db, client, auth):
    c, code = verified_chat(chat, in_house)
    r = c.say("Could I have a late checkout until 2 pm please?")
    assert r["outcome"] == "action_pending" and "NOT confirmed" in r["reply"]
    assert "approved" not in r["reply"].lower().replace("approve it", "")
    ap = db.scalar(select(ApprovalRequest))
    assert ap.status == "pending" and ap.kind == "late_checkout"
    assert ap.payload["confirmation_code"] == code and ap.payload["requested_until"] == "2 pm"
    assert "policy_excerpt" in ap.payload and "same_room_type_arrivals_on_checkout_day" in ap.payload

    assert "still waiting" in c.say("What's the status of my late checkout?")["reply"]
    again = c.say("late checkout please")
    assert "already have a late checkout request" in again["reply"]
    assert db.query(ApprovalRequest).count() == 1

    # department staff cannot decide; the front desk can
    assert client.post(f"{BASE}/approvals/{ap.id}/decide", json={"approve": True},
                       headers=auth("housekeeping@demo.hotel")).status_code == 403
    d = client.post(f"{BASE}/approvals/{ap.id}/decide", headers=auth("reception@demo.hotel"),
                    json={"approve": True, "approved_until": "1:00 PM",
                          "note": "Complimentary this time"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    out = c.say("what's the status of my late checkout?")["reply"]
    assert "approved until 1:00 PM" in out and "Complimentary this time" in out
    assert client.post(f"{BASE}/approvals/{ap.id}/decide", json={"approve": False},
                       headers=auth("manager@demo.hotel")).status_code == 409
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "approval.approved"))


def test_late_checkout_denial_is_reported(chat, in_house, client, auth, db):
    c, _ = verified_chat(chat, in_house)
    c.say("late checkout please")
    ap = db.scalar(select(ApprovalRequest))
    client.post(f"{BASE}/approvals/{ap.id}/decide", headers=auth("manager@demo.hotel"),
                json={"approve": False, "note": "Fully booked tomorrow"})
    out = c.say("status of my late checkout")["reply"]
    assert "not approved" in out and "Fully booked tomorrow" in out


# -------------------------------------------------------- workflow 5: complaints and emergencies

def test_serious_complaint_is_escalated_with_no_promises(chat, db, client, auth):
    c = chat()
    r = c.say("The staff were rude to me and I want a refund of my overcharged bill")
    assert r["outcome"] == "escalated"
    t = db.scalar(select(ServiceRequest).where(ServiceRequest.category == "complaint"))
    assert t.status.value == "escalated" and t.priority.value == "high"
    assert t.internal_notes[0]["text"].startswith("Escalation reason:")
    assert "can't promise" in r["reply"] and "refund will" not in r["reply"].lower()
    esc = db.scalar(select(AuditEvent).where(AuditEvent.action == "ticket.escalated"))
    assert esc and esc.entity_id == str(t.id)
    # visible to authorised staff, hidden from unrelated department staff
    ids = {x["id"] for x in client.get(f"{BASE}/tickets", headers=auth("reception@demo.hotel")).json()}
    assert str(t.id) in ids
    hk = {x["id"] for x in client.get(f"{BASE}/tickets", headers=auth("housekeeping@demo.hotel")).json()}
    assert str(t.id) not in hk


def test_safety_complaint_is_urgent_and_mild_complaint_is_not_escalated(chat, db):
    c = chat()
    c.say("I felt unsafe because someone tried the door of my room last night")
    c.say("The hallway carpet was a bit dirty near the lift, just wanted to mention it, awful")
    rows = {t.priority.value: t.status.value for t in db.scalars(
        select(ServiceRequest).where(ServiceRequest.category == "complaint"))}
    assert rows == {"urgent": "escalated", "normal": "open"}


def test_vague_complaint_asks_for_details_without_creating_a_ticket(chat, db):
    r = chat().say("I want to complain")
    assert r["outcome"] == "needs_info" and db.query(ServiceRequest).count() == 0


def test_emergency_escalates_immediately_and_stops_automation(chat, db):
    c = chat()
    r = c.say("There is smoke coming from the vent in room 305")
    assert r["outcome"] == "escalated" and "emergency number" in r["reply"]
    assert "+1-555-0100" in r["reply"]  # hotel-configured procedure, not model output
    t = db.scalar(select(ServiceRequest).where(ServiceRequest.category == "emergency"))
    assert t.priority.value == "urgent" and t.status.value == "escalated"
    assert db.get(Conversation, c.conversation_id).status == "handed_off"
    follow = c.say("book me a room for tomorrow")
    assert follow["outcome"] == "handoff" and "reservation" not in follow["reply"].lower()


# -------------------------------------------------------- workflow 6: manager analytics

def test_manager_summary_is_computed_from_records(chat, in_house, db, client, auth):
    c, _ = verified_chat(chat, in_house)
    c.say("my TV is not working")
    c.say("There are no towels, please send extra towels")
    chat().say("The staff were rude and I want a refund")
    tickets = db.scalars(select(ServiceRequest)).all()
    mgr = auth("manager@demo.hotel")
    r = client.post(f"{BASE}/ops/chat", headers=mgr, json={
        "text": "Give me a summary for the last 7 days", "client_message_id": "ops-1"}).json()
    s = client.get(f"{BASE}/analytics/summary", headers=mgr).json()
    assert s["open_requests"] == len(tickets) == 3
    assert s["escalations_in_window"] == 1 and s["complaints_in_window"] == 1
    assert s["complaint_themes"]["billing"] == 1 and s["overdue"] == 0
    assert s["active_reservations"] == 1
    assert f"Open requests now: {s['open_requests']}" in r["reply"]
    assert "escalations: 1" in r["reply"]
    # resolve one ticket and the average resolution time appears
    t = tickets[0]
    for st in ("in_progress", "resolved"):
        client.post(f"{BASE}/tickets/{t.id}/status", json={"status": st},
                    headers=auth("reception@demo.hotel") if t.category != "complaint" else mgr)
    s2 = client.get(f"{BASE}/analytics/summary?days=30", headers=mgr).json()
    assert s2["avg_resolution_hours"] is not None and s2["window_days"] == 30


def test_overdue_is_counted_from_due_dates(chat, in_house, db, client, auth):
    c, _ = verified_chat(chat, in_house)
    c.say("my TV is not working")
    t = db.scalar(select(ServiceRequest))
    t.due_at = t.created_at - timedelta(hours=1)
    db.commit()
    assert client.get(f"{BASE}/analytics/summary", headers=auth("manager@demo.hotel")
                      ).json()["overdue"] == 1


def test_analytics_permissions(chat, client, auth):
    rec = auth("reception@demo.hotel")
    assert client.get(f"{BASE}/analytics/summary", headers=rec).status_code == 403
    assert client.post(f"{BASE}/ops/chat", headers=rec, json={
        "text": "summary", "client_message_id": "x1"}).status_code == 403
    guest = chat()
    assert client.get(f"{BASE}/analytics/summary", headers=guest.headers).status_code == 401
    assert client.post(f"{BASE}/ops/chat", headers=guest.headers, json={
        "text": "summary", "client_message_id": "x2"}).status_code == 401
    # a staff token is not a guest session either
    assert client.post("/api/v1/chat/messages", headers=rec, json={
        "text": "hi", "client_message_id": "x3"}).status_code == 401


def test_ops_chat_stays_inside_approved_queries(client, auth, db):
    mgr = auth("manager@demo.hotel")
    r = client.post(f"{BASE}/ops/chat", headers=mgr, json={
        "text": "SELECT * FROM users; DROP TABLE guests", "client_message_id": "sql-1"}).json()
    assert "I can summarise operations" in r["reply"]
    assert db.query(User).count() == 5
    bad = client.post(f"{BASE}/ops/chat", headers=mgr, json={
        "text": "summary for the last 99 days", "client_message_id": "sql-2"}).json()
    assert "invalid" in bad["reply"].lower() or "access" in bad["reply"].lower()


# -------------------------------------------------------- workflow 7: handoff

def test_handoff_preserves_context_and_is_visible_to_staff(chat, db, client, auth):
    c = chat()
    c.say("What time is check-out?")
    r = c.say("I want to speak to a manager about my booking")
    assert r["outcome"] == "handoff"
    t = db.scalar(select(ServiceRequest).where(ServiceRequest.category == "handoff"))
    assert "What time is check-out?" in t.description and "speak to a manager" in t.description
    conv = db.get(Conversation, c.conversation_id)
    assert conv.status == "handed_off" and conv.state["handoff_ticket"] == t.ticket_number

    follow = c.say("hello?")
    assert follow["outcome"] == "handoff" and t.ticket_number in follow["reply"]

    rec = auth("reception@demo.hotel")
    lst = client.get(f"{BASE}/conversations?status=handed_off", headers=rec).json()
    assert [x["id"] for x in lst] == [str(c.conversation_id)]
    msgs = client.get(f"{BASE}/conversations/{c.conversation_id}/messages", headers=rec).json()
    assert any("manager" in m["content"] for m in msgs)
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "conversation.viewed"))
    sent = client.post(f"{BASE}/conversations/{c.conversation_id}/staff-messages", headers=rec,
                       json={"text": "Hi, this is Riley from the front desk."})
    assert sent.status_code == 201
    assert any(m["role"] == "staff" for m in c.history())
    # dept staff can't read guest conversations
    assert client.get(f"{BASE}/conversations", headers=auth("housekeeping@demo.hotel")
                      ).status_code == 403
    rel = client.post(f"{BASE}/conversations/{c.conversation_id}/release", headers=rec)
    assert rel.json()["status"] == "active"
    assert c.say("What time is check-out?")["outcome"] == "answered"


def test_guest_can_accept_handoff_offer_after_repeated_abstentions(chat, db):
    c = chat()
    c.say("Do you have a casino?")
    c.say("Is there a helipad?")
    r = c.say("yes")
    assert r["outcome"] == "handoff"
    assert db.scalar(select(ServiceRequest).where(ServiceRequest.category == "handoff"))


# -------------------------------------------------------- cancellation (confirmation required)

def test_cancellation_needs_verification_and_confirmation(chat, in_house, db):
    from app.models import Reservation
    code, rid = in_house
    c = chat()
    assert "verify" in c.say("Please cancel my reservation")["reply"].lower()
    c.verify(code, "Doe")
    # checked-in stays can't be cancelled here
    assert "confirmed" in c.say(f"Please cancel my reservation {code}")["reply"]
    assert db.get(Reservation, rid).status.value == "checked_in"


def test_confirmed_future_reservation_cancel_flow(chat, client, auth, db):
    from app.models import Reservation, RoomInventory
    from scripts.seed import uid
    rec = auth("reception@demo.hotel")
    g = client.post(f"{BASE}/guests", json={"full_name": "Pat Later"}, headers=rec).json()
    ci = date.today() + timedelta(days=10)
    res = client.post(f"{BASE}/reservations", headers=rec, json={
        "check_in": ci.isoformat(), "check_out": (ci + timedelta(days=1)).isoformat(),
        "adults": 1, "guest_id": g["id"], "room_type_id": str(uid("rt-standard"))}).json()
    c = chat()
    c.verify(res["confirmation_code"], "Later")
    r = c.say(f"Please cancel my reservation {res['confirmation_code']}")
    assert r["awaiting_confirmation"] and "48 hours" in r["reply"]  # policy from the KB
    assert db.get(Reservation, res["id"]).status.value == "confirmed"  # not yet
    done = c.say("yes")
    assert "now cancelled" in done["reply"]
    db.expire_all()
    assert db.get(Reservation, res["id"]).status.value == "cancelled"
    assert db.scalar(select(RoomInventory.booked).where(
        RoomInventory.room_type_id == uid("rt-standard"), RoomInventory.night == ci)) == 0


# -------------------------------------------------------- records

def test_agent_and_tool_executions_are_recorded_and_redacted(chat, in_house, db):
    c, _ = verified_chat(chat, in_house)
    c.say("My phone is +1 415 555 0199 and my TV is not working")
    ex = db.scalars(select(AgentExecution).order_by(AgentExecution.created_at)).all()[-1]
    assert ex.intent == "service_request" and ex.outcome == "action_done" and ex.steps >= 3
    assert ex.correlation_id and ex.latency_ms is not None
    assert [t["node"] for t in ex.trace][0] == "gate"
    tool = db.scalar(select(ToolExecution).where(
        ToolExecution.tool_name == "create_service_ticket"))
    assert tool.success and "415 555 0199" not in str(tool.arguments)
    assert "[phone]" in str(tool.arguments)


def test_card_numbers_are_never_stored(chat, db):
    c = chat()
    c.say("my card is 4111 1111 1111 1111, what time is check-out?")
    stored = db.scalars(select(Message.content).where(Message.role == "user")).all()
    assert all("4111" not in m for m in stored) and "card number removed" in stored[0]


def test_staff_login_still_works_alongside_guest_tokens(client, seeded):
    r = client.post("/api/v1/auth/login", json={"email": "manager@demo.hotel",
                                                "password": DEMO_PASSWORD})
    assert r.status_code == 200
