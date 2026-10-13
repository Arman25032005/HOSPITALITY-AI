import threading
import uuid
from datetime import date, timedelta

from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.models import AuditEvent, Reservation, RoomInventory
from app.services import reservations as resv
from app.services.errors import ConflictError
from scripts.seed import uid
from tests.conftest import HOTEL_ID, stay

BASE = f"/api/v1/hotels/{HOTEL_ID}"
STD = str(uid("rt-standard"))
SUITE = str(uid("rt-suite"))


def book(client, headers, guest_id, rt=STD, key=None, **kw):
    h = dict(headers)
    if key:
        h["Idempotency-Key"] = key
    body = {**stay(**kw), "adults": 2, "guest_id": str(guest_id), "room_type_id": rt}
    return client.post(f"{BASE}/reservations", json=body, headers=h)


def test_availability_lists_options_with_total_price(client, auth):
    r = client.get(f"{BASE}/availability", params={**stay(), "adults": 3},
                   headers=auth("reception@demo.hotel"))
    assert r.status_code == 200
    codes = {o["code"]: o for o in r.json()}
    assert "standard" not in codes  # sleeps 2 only
    assert codes["deluxe"]["total_price"] == "440.00"  # 2 nights x 220
    assert codes["deluxe"]["available"] == 4


def test_create_persists_and_decrements_inventory(client, auth, guest_id, db):
    h = auth("reception@demo.hotel")
    r = book(client, h, guest_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "confirmed" and body["total_price"] == "300.00"
    got = client.get(f"{BASE}/reservations/{body['id']}", headers=h)
    assert got.json()["confirmation_code"] == body["confirmation_code"]
    inv = db.scalars(select(RoomInventory).where(RoomInventory.room_type_id == uuid.UUID(STD),
                                                 RoomInventory.booked > 0)).all()
    assert len(inv) == 2 and all(i.booked == 1 for i in inv)
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "reservation.created"))


def test_idempotency_key_returns_same_reservation(client, auth, guest_id, db):
    h = auth("reception@demo.hotel")
    a = book(client, h, guest_id, key="k1")
    b = book(client, h, guest_id, key="k1")
    assert a.json()["id"] == b.json()["id"]
    assert db.query(Reservation).count() == 1
    assert db.scalar(select(RoomInventory.booked).where(
        RoomInventory.room_type_id == uuid.UUID(STD)).order_by(RoomInventory.booked.desc())) == 1


def test_idempotency_key_reuse_with_different_params_conflicts(client, auth, guest_id):
    h = auth("reception@demo.hotel")
    book(client, h, guest_id, key="k2")
    assert book(client, h, guest_id, key="k2", nights=3).status_code == 409


def test_sold_out_returns_409_and_does_not_overbook(client, auth, guest_id, db):
    h = auth("reception@demo.hotel")
    assert book(client, h, guest_id, rt=SUITE).status_code == 201
    assert book(client, h, guest_id, rt=SUITE).status_code == 201  # suites: total 2
    assert book(client, h, guest_id, rt=SUITE).status_code == 409
    assert db.query(Reservation).count() == 2


def test_cancel_restores_inventory_and_is_idempotent(client, auth, guest_id, db):
    h = auth("reception@demo.hotel")
    rid = book(client, h, guest_id).json()["id"]
    assert client.post(f"{BASE}/reservations/{rid}/cancel", headers=h).json()["status"] == \
        "cancelled"
    assert client.post(f"{BASE}/reservations/{rid}/cancel", headers=h).status_code == 200
    assert db.scalar(select(RoomInventory.booked).where(
        RoomInventory.room_type_id == uuid.UUID(STD)).order_by(RoomInventory.booked.desc())) == 0


def test_validation_rejects_bad_requests(client, auth, guest_id):
    h = auth("reception@demo.hotel")
    past = {"check_in": (date.today() - timedelta(days=1)).isoformat(),
            "check_out": date.today().isoformat(), "adults": 1, "guest_id": str(guest_id),
            "room_type_id": STD}
    assert client.post(f"{BASE}/reservations", json=past, headers=h).status_code == 422
    inverted = {**past, "check_in": "2030-01-05", "check_out": "2030-01-02"}
    assert client.post(f"{BASE}/reservations", json=inverted, headers=h).status_code == 422
    too_many = {**stay(), "adults": 3, "guest_id": str(guest_id), "room_type_id": STD}
    assert client.post(f"{BASE}/reservations", json=too_many, headers=h).status_code == 422


def test_department_staff_cannot_book(client, auth, guest_id):
    r = book(client, auth("housekeeping@demo.hotel"), guest_id)
    assert r.status_code in (403, 404)


def test_concurrent_bookings_of_last_room_exactly_one_wins(seeded, guest_id, db):
    ci = date.today() + timedelta(days=20)
    co = ci + timedelta(days=1)
    db.execute(text("UPDATE room_inventory SET total = 1 WHERE room_type_id = :rt AND night = :n"),
               {"rt": SUITE, "n": ci})
    db.commit()
    org_id = db.scalar(text("SELECT organization_id FROM hotels WHERE id = :h"), {"h": HOTEL_ID})
    results: list[str] = []
    barrier = threading.Barrier(6)

    def worker():
        with SessionLocal() as s:
            barrier.wait()
            try:
                resv.create_reservation(s, org_id=org_id, hotel_id=HOTEL_ID, guest_id=guest_id,
                                        room_type_id=uuid.UUID(SUITE), check_in=ci,
                                        check_out=co, adults=2)
                results.append("ok")
            except ConflictError:
                results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["conflict"] * 5 + ["ok"]
    db.expire_all()
    assert db.scalar(select(RoomInventory.booked).where(
        RoomInventory.room_type_id == uuid.UUID(SUITE), RoomInventory.night == ci)) == 1
