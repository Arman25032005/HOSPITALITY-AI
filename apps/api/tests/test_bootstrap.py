import json
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text

from app.models import Hotel, Room, RoomInventory
from app.services.errors import ValidationFailed
from scripts import bootstrap as bs

ROOMS = [{"code": "std", "name": "Standard", "max_occupancy": 2, "base_rate": "120.00", "count": 3},
         {"code": "dlx", "name": "Deluxe", "max_occupancy": 3, "base_rate": "200.00", "count": 2}]
PW = "Harbor-Lantern-Quartz-42"


@pytest.fixture()
def clean(db):
    db.execute(text("TRUNCATE organizations CASCADE"))
    db.commit()
    yield
    db.execute(text("TRUNCATE organizations CASCADE"))
    db.commit()


def run(**over):
    args = dict(org_name="Harbor", org_slug="harbor", hotel_name="Harbor Inn", hotel_slug="harbor-inn",
                admin_email="ops@harbor.example", admin_name="Ops Admin", admin_password=PW,
                rooms=ROOMS, emergency_instructions="Call 112 now.", inventory_days=10)
    return bs.bootstrap(**{**args, **over})


def test_bootstrapped_hotel_is_immediately_usable(clean, client, db):
    out = run()
    assert out["inventory_nights_added"] == 20 and out["room_types"] == 2
    # the admin can sign in and sees exactly their hotel
    r = client.post("/api/v1/auth/login", json={"email": "ops@harbor.example", "password": PW})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    hotels = client.get("/api/v1/hotels", headers=h).json()
    assert [x["slug"] for x in hotels] == ["harbor-inn"]
    hid = hotels[0]["id"]
    # guests can find it, and the stay can actually be booked against the generated inventory
    assert {"org_slug": "harbor", "hotel_slug": "harbor-inn", "name": "Harbor Inn"} in \
        client.get("/api/v1/public/hotels").json()
    ci = date.today() + timedelta(days=2)
    avail = client.get(f"/api/v1/hotels/{hid}/availability", headers=h, params={
        "check_in": ci.isoformat(), "check_out": (ci + timedelta(days=2)).isoformat(), "adults": 2})
    assert avail.status_code == 200 and {t["name"] for t in avail.json()} == {"Standard", "Deluxe"}
    g = client.post(f"/api/v1/hotels/{hid}/guests", json={"full_name": "First Guest"}, headers=h)
    res = client.post(f"/api/v1/hotels/{hid}/reservations", headers=h, json={
        "check_in": ci.isoformat(), "check_out": (ci + timedelta(days=2)).isoformat(), "adults": 2,
        "guest_id": g.json()["id"], "room_type_id": avail.json()[0]["room_type_id"]})
    assert res.status_code == 201, res.text
    # departments exist for ticket routing; emergency text is stored for the agent
    assert len(client.get(f"/api/v1/hotels/{hid}/departments", headers=h).json()) == 4
    assert db.scalar(select(Hotel.settings).where(Hotel.slug == "harbor-inn"))[
        "emergency_instructions"] == "Call 112 now."


def test_room_numbers_are_unique_even_when_types_share_a_floor(clean, db):
    run()
    nums = db.scalars(select(Room.number)).all()
    assert len(nums) == len(set(nums)) == 5


def test_refuses_weak_passwords_duplicates_and_missing_emergency_text(clean, db):
    with pytest.raises(ValidationFailed):
        run(admin_password="password1234")
    with pytest.raises(ValidationFailed):
        run(emergency_instructions="  ")
    assert db.scalar(select(func.count()).select_from(Hotel)) == 0  # nothing half-created
    run()
    with pytest.raises(ValidationFailed, match="already exists"):
        run()


def test_extend_inventory_is_idempotent_and_keeps_bookings(clean, db):
    run(inventory_days=5)
    hotel = db.scalar(select(Hotel))
    row = db.scalar(select(RoomInventory).order_by(RoomInventory.night))
    row.booked = 1
    db.commit()
    assert bs.extend_inventory(db, hotel, 5) == 0  # nothing missing
    assert bs.extend_inventory(db, hotel, 8) == 6  # 3 more nights x 2 room types
    db.expire_all()
    assert db.get(RoomInventory, row.id).booked == 1


def test_rooms_file_validation(tmp_path):
    bad = tmp_path / "r.json"
    bad.write_text(json.dumps([{"code": "a", "name": "A"}]))
    with pytest.raises(ValidationFailed):
        bs.load_rooms(str(bad))
    bad.write_text(json.dumps(ROOMS + [ROOMS[0]]))
    with pytest.raises(ValidationFailed, match="duplicate"):
        bs.load_rooms(str(bad))
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(ROOMS))
    assert len(bs.load_rooms(str(ok))) == 2
