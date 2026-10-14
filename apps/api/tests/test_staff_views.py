import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models import AuditEvent, Reservation, RoomInventory, ServiceRequest
from scripts.seed import uid
from tests.conftest import DEMO_PASSWORD, HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}"
DELUXE, SUITE, STD = str(uid("rt-deluxe")), str(uid("rt-suite")), str(uid("rt-standard"))


def d(offset):
    return date.today() + timedelta(days=offset)


@pytest.fixture()
def book(client, auth):
    rec = auth("reception@demo.hotel")

    def _book(name, offset=10, nights=2, rt=DELUXE, adults=2):
        g = client.post(f"{BASE}/guests", json={"full_name": name, "email": f"{name[0]}@x.test"},
                        headers=rec).json()
        r = client.post(f"{BASE}/reservations", headers=rec, json={
            "check_in": str(d(offset)), "check_out": str(d(offset + nights)), "adults": adults,
            "guest_id": g["id"], "room_type_id": rt})
        assert r.status_code == 201, r.text
        return r.json()

    return _book


# ------------------------------------------------------------------ reservations

def test_search_filters_pagination_and_like_escaping(client, auth, book):
    a = book("Alice Wong", 10)
    b = book("Bob Smith", 20)
    book("Carol Wong", 30)
    rec = auth("reception@demo.hotel")

    def find(**p):
        r = client.get(f"{BASE}/reservations", params=p, headers=rec)
        assert r.status_code == 200, r.text
        return r.json()

    assert {x["guest"]["full_name"] for x in find(q="wong")} == {"Alice Wong", "Carol Wong"}
    assert [x["id"] for x in find(q=a["confirmation_code"][2:6].lower())] == [a["id"]]
    assert [x["id"] for x in find(arrival_from=str(d(15)), arrival_to=str(d(25)))] == [b["id"]]
    assert len(find()) == 3 and len(find(limit=2)) == 2 and len(find(limit=2, offset=2)) == 1
    assert find(q="%") == [] and find(q="_") == []  # wildcards are literals, not match-all
    assert find(status="cancelled") == []
    row = find(q="Bob")[0]
    assert row["room_type_name"] == "Deluxe King" and row["room_number"] is None
    assert row["guest"]["email"] == "B@x.test".lower() or row["guest"]["email"].lower() == "b@x.test"
    # newest arrivals first
    assert [x["guest"]["full_name"] for x in find()] == ["Carol Wong", "Bob Smith", "Alice Wong"]


def test_reservation_views_are_role_and_tenant_scoped(client, auth, book, other_org):
    r = book("Alice Wong")
    assert client.get(f"{BASE}/reservations", headers=auth("housekeeping@demo.hotel")
                      ).status_code == 403
    tok = client.post("/api/v1/auth/login", json={"email": other_org["email"],
                                                  "password": DEMO_PASSWORD}).json()["access_token"]
    other = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"{BASE}/reservations", headers=other).status_code == 404
    assert client.get(f"{BASE}/reservations/{r['id']}/history", headers=other).status_code == 404
    assert client.post(f"{BASE}/reservations/{r['id']}/change", headers=other, json={
        "check_in": str(d(40)), "check_out": str(d(42)), "adults": 1}).status_code == 404


def test_history_records_who_did_what(client, auth, book):
    r = book("Alice Wong", offset=0)
    rec, mgr = auth("reception@demo.hotel"), auth("manager@demo.hotel")
    client.post(f"{BASE}/reservations/{r['id']}/check-in", headers=rec, json={})
    client.post(f"{BASE}/reservations/{r['id']}/change", headers=mgr, json={
        "check_in": str(d(0)), "check_out": str(d(3)), "adults": 2}) 
    hist = client.get(f"{BASE}/reservations/{r['id']}/history", headers=rec).json()
    assert [h["action"] for h in hist] == ["reservation.created", "reservation.checked_in"]
    assert hist[0]["actor_name"] == "Riley Reception"
    detail = client.get(f"{BASE}/reservations", params={"q": "Alice"}, headers=rec).json()[0]
    assert detail["status"] == "checked_in" and detail["room_number"] is not None


def inv(db, rt, night):
    db.expire_all()
    return db.scalar(select(RoomInventory.booked).where(RoomInventory.room_type_id == uuid_(rt),
                                                        RoomInventory.night == night))


def uuid_(s):
    import uuid
    return uuid.UUID(s)


def test_change_moves_inventory_reprices_and_audits(client, auth, book, db):
    r = book("Alice Wong", offset=10, nights=2)
    mgr = auth("manager@demo.hotel")
    out = client.post(f"{BASE}/reservations/{r['id']}/change", headers=mgr, json={
        "check_in": str(d(11)), "check_out": str(d(14)), "adults": 3})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["check_in"] == str(d(11)) and body["adults"] == 3 and body["total_price"] == "660.00"
    assert inv(db, DELUXE, d(10)) == 0          # released
    assert inv(db, DELUXE, d(11)) == 1 and inv(db, DELUXE, d(13)) == 1   # kept + added
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "reservation.changed"))
    assert ev.details["before"]["check_in"] == str(d(10)) and ev.details["after"]["adults"] == 3


def test_change_rejections_leave_everything_untouched(client, auth, book, db):
    r = book("Alice Wong", offset=10, nights=2)
    other = book("Bob Smith", offset=50, nights=1, rt=SUITE)
    book("Cara Lee", offset=50, nights=1, rt=SUITE)  # suites: total 2 -> now sold out on d(50)
    mgr = auth("manager@demo.hotel")
    sold = client.post(f"{BASE}/reservations/{other['id']}/change", headers=mgr, json={
        "check_in": str(d(50)), "check_out": str(d(51)), "adults": 2})
    assert sold.status_code == 200  # same dates: nothing newly added
    to_sold_out = client.post(f"{BASE}/reservations/{r['id']}/change", headers=mgr, json={
        "check_in": str(d(10)), "check_out": str(d(12)), "adults": 5})
    assert to_sold_out.status_code == 422  # deluxe sleeps 3
    past = client.post(f"{BASE}/reservations/{r['id']}/change", headers=mgr, json={
        "check_in": str(d(-3)), "check_out": str(d(-1)), "adults": 2})
    assert past.status_code == 422
    bad = client.post(f"{BASE}/reservations/{r['id']}/change", headers=mgr, json={
        "check_in": str(d(12)), "check_out": str(d(11)), "adults": 2})
    assert bad.status_code == 422
    assert inv(db, DELUXE, d(10)) == 1 and inv(db, DELUXE, d(11)) == 1
    client.post(f"{BASE}/reservations/{r['id']}/cancel", headers=mgr)
    assert client.post(f"{BASE}/reservations/{r['id']}/change", headers=mgr, json={
        "check_in": str(d(10)), "check_out": str(d(12)), "adults": 2}).status_code == 409
    assert client.post(f"{BASE}/reservations/{r['id']}/change",
                       headers=auth("housekeeping@demo.hotel"), json={
                           "check_in": str(d(10)), "check_out": str(d(12)), "adults": 2}
                       ).status_code == 403


def test_change_into_a_full_night_conflicts_atomically(client, auth, book, db):
    a = book("Alice Wong", offset=10, nights=1, rt=SUITE)
    book("Bob Smith", offset=11, nights=1, rt=SUITE)
    book("Cara Lee", offset=11, nights=1, rt=SUITE)   # d(11) is now full
    mgr = auth("manager@demo.hotel")
    r = client.post(f"{BASE}/reservations/{a['id']}/change", headers=mgr, json={
        "check_in": str(d(10)), "check_out": str(d(12)), "adults": 2})
    assert r.status_code == 409
    assert inv(db, SUITE, d(10)) == 1 and inv(db, SUITE, d(11)) == 2   # unchanged
    db.expire_all()
    assert str(db.get(Reservation, uuid_(a["id"])).check_out) == str(d(11))


def test_concurrent_changes_cannot_overbook_a_night(client, auth, book, db):
    a = book("Alice Wong", offset=10, nights=1, rt=SUITE)
    b = book("Bob Smith", offset=12, nights=1, rt=SUITE)
    c = book("Cara Lee", offset=14, nights=1, rt=SUITE)
    db.execute(text("UPDATE room_inventory SET total = 1 WHERE room_type_id = :rt AND night = :n"),
               {"rt": SUITE, "n": d(20)})
    db.commit()
    mgr = auth("manager@demo.hotel")
    results, barrier = [], threading.Barrier(3)

    def go(res):
        barrier.wait()
        r = client.post(f"{BASE}/reservations/{res['id']}/change", headers=mgr, json={
            "check_in": str(d(20)), "check_out": str(d(21)), "adults": 2})
        results.append(r.status_code)

    ts = [threading.Thread(target=go, args=(x,)) for x in (a, b, c)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 409, 409]
    assert inv(db, SUITE, d(20)) == 1


def test_inventory_view(client, auth, book):
    book("Alice Wong", offset=3, nights=2)
    rows = client.get(f"{BASE}/inventory", params={"from": str(d(2)), "to": str(d(5))},
                      headers=auth("reception@demo.hotel")).json()
    assert [r["room_type_name"] for r in rows] == ["Standard Queen", "Deluxe King", "Executive Suite"]
    deluxe = next(r for r in rows if r["room_type_name"] == "Deluxe King")
    assert [(n["night"], n["booked"], n["total"]) for n in deluxe["nights"]] == [
        (str(d(2)), 0, 4), (str(d(3)), 1, 4), (str(d(4)), 1, 4), (str(d(5)), 0, 4)]
    assert client.get(f"{BASE}/inventory", params={"from": str(d(0)), "to": str(d(200))},
                      headers=auth("reception@demo.hotel")).status_code == 422
    assert client.get(f"{BASE}/inventory", headers=auth("housekeeping@demo.hotel")).status_code == 403


# ------------------------------------------------------------------ staff directory

def test_staff_and_department_lists(client, auth):
    rec = auth("reception@demo.hotel")
    staff = {s["full_name"]: s for s in client.get(f"{BASE}/staff", headers=rec).json()}
    assert set(staff) == {"Dana Admin", "Morgan Manager", "Riley Reception",
                          "Hayden Housekeeping", "Max Maintenance"}
    assert staff["Hayden Housekeeping"]["departments"] == ["Housekeeping"]
    assert "password_hash" not in str(staff) and "email" not in str(staff)
    depts = client.get(f"{BASE}/departments", headers=auth("housekeeping@demo.hotel")).json()
    assert {x["code"] for x in depts} == {"front_desk", "housekeeping", "maintenance", "food_beverage"}
    assert client.get(f"{BASE}/staff", headers=auth("housekeeping@demo.hotel")).status_code == 403


# ------------------------------------------------------------------ tickets

@pytest.fixture()
def tickets(client, auth, db):
    rec = auth("reception@demo.hotel")
    mk = lambda cat, desc, room=None, pr="normal": client.post(  # noqa: E731
        f"{BASE}/tickets", headers=rec, json={"category": cat, "description": desc,
                                              "room_number": room, "priority": pr}).json()
    out = {"ac": mk("maintenance", "AC is broken in the room", "205", "high"),
           "towels": mk("housekeeping", "Need extra towels", "310"),
           "tv": mk("maintenance", "TV remote 100% dead", "205")}
    return out


def test_ticket_search_and_filters(client, auth, tickets, db):
    rec = auth("reception@demo.hotel")

    def find(**p):
        r = client.get(f"{BASE}/tickets-search", params=p, headers=rec)
        assert r.status_code == 200, r.text
        return {t["ticket_number"] for t in r.json()}

    ac, towels, tv = (tickets[k]["ticket_number"] for k in ("ac", "towels", "tv"))
    assert find(q="towels") == {towels} and find(q=ac[2:7].lower()) == {ac}
    assert find(q="205") == {ac, tv} and find(q="100%") == {tv}
    assert find(q="%") == {tv}      # literal percent sign: only the ticket that contains one
    assert find(q="_") == set() and find(q="%%") == set()
    assert find(priority="high") == {ac} and find(status="open") == {ac, towels, tv}
    assert find(created_from=str(date.today()), created_to=str(date.today())) == {ac, towels, tv}
    assert find(created_to=str(date.today() - timedelta(days=1))) == set()
    mx = db.scalar(select(ServiceRequest).where(ServiceRequest.ticket_number == ac))
    mx.due_at = datetime.now(UTC) - timedelta(hours=2)
    db.commit()
    assert find(overdue=True) == {ac}
    dept = {x["code"]: x["id"] for x in client.get(f"{BASE}/departments", headers=rec).json()}
    assert find(department_id=dept["housekeeping"]) == {towels}
    row = client.get(f"{BASE}/tickets-search", params={"q": towels}, headers=rec).json()[0]
    assert row["department_name"] == "Housekeeping" and row["assignee_name"] is None
    assert len(client.get(f"{BASE}/tickets-search", params={"limit": 2}, headers=rec).json()) == 2


def test_ticket_search_respects_department_scope_and_assignment_names(client, auth, tickets, db):
    rec, mx = auth("reception@demo.hotel"), auth("maintenance@demo.hotel")
    maint = {r["ticket_number"] for r in client.get(f"{BASE}/tickets-search", headers=mx).json()}
    assert maint == {tickets["ac"]["ticket_number"], tickets["tv"]["ticket_number"]}
    staff = {s["full_name"]: s["id"] for s in client.get(f"{BASE}/staff", headers=rec).json()}
    client.post(f"{BASE}/tickets/{tickets['ac']['id']}/assign", headers=rec,
                json={"assignee_id": staff["Max Maintenance"]})
    rows = client.get(f"{BASE}/tickets-search", params={"assigned_to": staff["Max Maintenance"]},
                      headers=rec).json()
    assert [r["assignee_name"] for r in rows] == ["Max Maintenance"]


def test_ticket_history_and_conversation_link(client, auth, tickets, chat, in_house, db):
    rec = auth("reception@demo.hotel")
    t = tickets["ac"]
    client.post(f"{BASE}/tickets/{t['id']}/status", json={"status": "in_progress"}, headers=rec)
    client.post(f"{BASE}/tickets/{t['id']}/notes", json={"note": "Parts ordered"}, headers=rec)
    hist = client.get(f"{BASE}/tickets/{t['id']}/history", headers=rec).json()
    assert [h["action"] for h in hist] == ["ticket.created", "ticket.status_changed",
                                          "ticket.note_added"]
    assert hist[1]["actor_name"] == "Riley Reception"
    assert client.get(f"{BASE}/tickets/{t['id']}/conversation", headers=rec).json() == {
        "conversation_id": None}

    c = chat()
    c.verify(in_house[0], "Doe")
    number = c.say("my TV is not working")["ticket_numbers"][0]
    tid = db.scalar(select(ServiceRequest.id).where(ServiceRequest.ticket_number == number))
    link = client.get(f"{BASE}/tickets/{tid}/conversation", headers=rec).json()
    assert link["conversation_id"] == c.conversation_id
    mx = auth("maintenance@demo.hotel")
    assert client.get(f"{BASE}/tickets/{tid}/conversation", headers=mx).status_code == 403
    assert client.get(f"{BASE}/tickets/{tickets['towels']['id']}/history", headers=mx
                      ).status_code == 404  # housekeeping ticket invisible to maintenance


def test_dashboard_series_include_feedback_and_daily_counts(client, auth, tickets, chat, db):
    c = chat()
    r = c.say("What time is check-out?")
    client.post(f"/api/v1/chat/messages/{r['message_id']}/feedback", headers=c.headers,
                json={"rating": 4})
    s = client.get(f"{BASE}/analytics/summary", headers=auth("manager@demo.hotel")).json()
    assert s["feedback_trend"] == [{"date": str(date.today()), "avg_rating": 4.0, "count": 1}]
    assert s["tickets_per_day"] == [{"date": str(date.today()), "count": 3}]
    assert s["avg_feedback_rating"] == 4.0
