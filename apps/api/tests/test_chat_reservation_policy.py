import threading
from datetime import date, datetime, timedelta, UTC

from sqlalchemy import select, text

from app.llm.base import LLMUnavailable
from app.llm.scripted import ScriptedLLM
from app.models import Conversation, GuestFeedback, Reservation, RoomInventory
from app.services import reservations as resv
from tests.conftest import HOTEL_ID
from scripts.seed import uid

SUITE = uid("rt-suite")


def dates(offset=20, nights=2):
    ci = date.today() + timedelta(days=offset)
    return ci, ci + timedelta(days=nights)


# -------------------------------------------------------- workflow 1: policy question

def test_policy_question_is_grounded_cited_and_persisted(chat, db):
    c = chat()
    r = c.say("What time is check-out?")
    assert r["outcome"] == "answered" and "11:00 AM" in r["reply"]
    assert r["citations"] and r["citations"][0]["title"] == "Stay Policies"
    hist = c.history()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["citations"][0]["document_id"] == r["citations"][0]["document_id"]


def test_feedback_only_on_own_assistant_messages(chat, client, db):
    a, b = chat(), chat()
    r = a.say("Is there free wifi?")
    ok = client.post(f"/api/v1/chat/messages/{r['message_id']}/feedback", headers=a.headers,
                     json={"rating": 5, "comment": "great"})
    assert ok.status_code == 201
    assert db.query(GuestFeedback).count() == 1
    other = client.post(f"/api/v1/chat/messages/{r['message_id']}/feedback", headers=b.headers,
                        json={"rating": 1})
    assert other.status_code == 404
    bad = client.post(f"/api/v1/chat/messages/{r['message_id']}/feedback", headers=a.headers,
                      json={"rating": 9})
    assert bad.status_code == 422


def test_unsupported_question_abstains_then_offers_handoff(chat):
    c = chat()
    first = c.say("Do you allow helicopter landings on the roof?")
    assert first["outcome"] == "abstained" and not first["awaiting_confirmation"]
    second = c.say("Do you have a casino?")
    assert second["awaiting_confirmation"] and "connect you" in second["reply"]


def test_guest_never_receives_confidential_policy(chat):
    r = chat().say("What is the goodwill credit limit for staff?")
    assert r["outcome"] == "abstained" and "50 USD" not in r["reply"]


# -------------------------------------------------------- workflow 2: reservation

def reserve_to_pending(c, ci, co):
    assert "check-in and check-out dates" in c.say("I'd like to book a room")["reply"]
    opts = c.say(f"{ci} to {co} for 2 adults")["reply"]
    assert "Deluxe King" in opts and "440.00 USD" in opts
    assert "name" in c.say("Deluxe King")["reply"].lower()
    pending = c.say("Jane Doe")
    assert pending["awaiting_confirmation"] and "Reply YES" in pending["reply"]
    return pending


def test_reservation_is_not_created_until_explicit_confirmation(chat, db):
    ci, co = dates()
    c = chat()
    pending = reserve_to_pending(c, ci, co)
    assert db.query(Reservation).count() == 0, "nothing may be booked before YES"
    assert "440.00 USD" in pending["reply"] and "Jane Doe" in pending["reply"]
    maybe = c.say("hmm maybe")
    assert maybe["awaiting_confirmation"] and db.query(Reservation).count() == 0
    done = c.say("yes")
    assert done["outcome"] == "action_done" and len(done["reservation_codes"]) == 1
    code = done["reservation_codes"][0]
    assert code in done["reply"]
    db.expire_all()
    res = db.scalar(select(Reservation).where(Reservation.confirmation_code == code))
    assert res and res.status.value == "confirmed" and str(res.total_price) == "440.00"
    booked = db.scalars(select(RoomInventory.booked).where(
        RoomInventory.room_type_id == uid("rt-deluxe"), RoomInventory.night == ci)).one()
    assert booked == 1


def test_declining_creates_nothing(chat, db):
    ci, co = dates()
    c = chat()
    reserve_to_pending(c, ci, co)
    r = c.say("no")
    assert "haven't booked anything" in r["reply"] and db.query(Reservation).count() == 0
    assert c.say("yes")["outcome"] != "action_done"  # nothing pending any more
    assert db.query(Reservation).count() == 0


def test_yes_without_pending_action_does_nothing(chat, db):
    r = chat().say("yes")
    assert r["outcome"] != "action_done" and db.query(Reservation).count() == 0


def test_expired_confirmation_is_not_executed(chat, db):
    ci, co = dates()
    c = chat()
    reserve_to_pending(c, ci, co)
    conv = db.get(Conversation, c.conversation_id)
    conv.state = {**conv.state, "pending_action": {**conv.state["pending_action"],
                  "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}}
    db.commit()
    r = c.say("yes")
    assert "expired" in r["reply"] and r["outcome"] != "action_done"
    assert db.query(Reservation).count() == 0


def test_duplicate_confirmation_delivery_books_once(chat, db):
    ci, co = dates()
    c = chat()
    reserve_to_pending(c, ci, co)
    a = c.say("yes", message_id="dup-1")
    b = c.say("yes", message_id="dup-1")
    assert a["reply"] == b["reply"] and a["message_id"] == b["message_id"]
    assert db.query(Reservation).count() == 1


def test_concurrent_duplicate_deliveries_book_once(chat, db, client):
    ci, co = dates()
    c = chat()
    reserve_to_pending(c, ci, co)
    barrier, out = threading.Barrier(4), []

    def go():
        barrier.wait()
        r = client.post("/api/v1/chat/messages", headers=c.headers,
                        json={"text": "yes", "client_message_id": "race-1"})
        out.append(r.status_code)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(s == 200 for s in out), out
    assert db.query(Reservation).count() == 1


def test_inventory_taken_between_proposal_and_confirmation(chat, db):
    ci, co = dates()
    c = chat()
    c.say("I'd like to book a suite")
    c.say(f"{ci} to {co} for 2 adults")
    c.say("Executive Suite")
    pending = c.say("Jane Doe")
    assert pending["awaiting_confirmation"]
    # someone else takes both suites before the guest confirms
    guest_id = db.scalar(text("select id from guests limit 1"))
    org = db.scalar(text("select organization_id from hotels where id=:h"), {"h": HOTEL_ID})
    for _ in range(2):
        resv.create_reservation(db, org_id=org, hotel_id=HOTEL_ID, guest_id=guest_id,
                                room_type_id=SUITE, check_in=ci, check_out=co, adults=2)
    r = c.say("yes")
    assert "no longer available" in r["reply"] and not r["reservation_codes"]
    assert db.query(Reservation).count() == 2


def test_validation_of_dates_and_party_size(chat):
    c = chat()
    past = (date.today() - timedelta(days=3)).isoformat()
    r = c.say(f"book a room {past} to {date.today().isoformat()} for 2 adults")
    assert "in the past" in r["reply"]
    ci, _ = dates()
    r = c.say(f"book a room from {ci} to {ci - timedelta(days=1)} for 2 adults")
    assert "after check-in" in r["reply"]
    r = c.say(f"book a room from {ci} to {ci + timedelta(days=2)} for 9 adults")
    assert "no rooms" in r["reply"].lower()


def test_month_name_dates_without_llm(chat):
    ci, co = dates(60, 2)
    c = chat()
    r = c.say(f"Book a room from {ci.day} {ci.strftime('%B')} to {co.day} {co.strftime('%B')} "
              "for two adults")
    assert str(ci) in r["reply"] and "Standard Queen" in r["reply"]


def test_natural_language_dates_use_llm_and_validate_output(chat):
    ci, co = dates(25, 2)

    def script(schema, text_):
        if schema == "StaySlots":
            return __import__("app.agents.llm_tasks", fromlist=["StaySlots"]).StaySlots(
                check_in=ci.isoformat(), check_out=co.isoformat(), adults=2)
        return LLMUnavailable("down")

    llm = ScriptedLLM(script)
    from app.main import app
    from app.api.routes.chat import get_llm_dep
    app.dependency_overrides[get_llm_dep] = lambda: llm
    r = chat().say("book us a room for the weekend after next, two of us")
    assert str(ci) in r["reply"] and "Deluxe King" in r["reply"]
    assert any(call["schema"] == "StaySlots" for call in llm.calls)
    assert "weekend after next" in llm.calls[0]["messages"][-1]["content"]


def test_llm_garbage_or_outage_degrades_to_asking(chat, use_llm):
    from app.agents.llm_tasks import StaySlots
    past = (date.today() - timedelta(days=30)).isoformat()
    use_llm(ScriptedLLM(lambda s, t: StaySlots(check_in=past, check_out=past, adults=99)))
    assert "dates" in chat().say("book me something nice for sometime")["reply"]
    use_llm(ScriptedLLM(lambda s, t: LLMUnavailable("down")))
    r = chat().say("book me something nice for sometime")
    assert "dates" in r["reply"] and r["outcome"] == "needs_info"


def test_two_guests_have_isolated_booking_flows(chat):
    ci, co = dates()
    a, b = chat(), chat()
    a.say("I'd like to book a room")
    a.say(f"{ci} to {co} for 2 adults")
    r = b.say("Deluxe King")  # B never saw the options
    assert r["outcome"] != "action_pending" and not r["awaiting_confirmation"]
    assert "available from" not in r["reply"]  # B never sees A's search results
    assert b.history()[0]["content"] == "Deluxe King"
