"""Provision a real (non-demo) organization: hotel, departments, room types, rooms, inventory
and the first administrator.

  BOOTSTRAP_ADMIN_PASSWORD=... python -m scripts.bootstrap \\
      --org-name "Harbor Hotels" --org-slug harbor --hotel-name "Harbor Inn" --hotel-slug harbor-inn \\
      --admin-email ops@example.com --admin-name "Ops Admin" --rooms rooms.json \\
      --emergency-instructions "Call 112 now." [--front-desk-phone +1-555-0100] [--inventory-days 365]

rooms.json: [{"code": "std", "name": "Standard Queen", "max_occupancy": 2, "base_rate": "150.00",
              "count": 6, "floor": 1}, ...]   (room numbers are generated as <floor><NN>)

The admin password comes from BOOTSTRAP_ADMIN_PASSWORD or an interactive prompt, never from argv
(shell history, process list). It must satisfy the same policy as every other password.
Refuses to run if the organization slug already exists. Other commands:
  python -m scripts.bootstrap extend-inventory --hotel-slug harbor-inn --days 365
which idempotently publishes more bookable nights (run it on a schedule, e.g. monthly).
"""
import argparse
import getpass
import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import (
    Department, Hotel, Organization, Role, Room, RoomInventory, RoomType, StaffAssignment, User,
)
from app.services.errors import ValidationFailed
from app.services.users import validate_password

DEPARTMENTS = [("front_desk", "Front Desk"), ("housekeeping", "Housekeeping"),
               ("maintenance", "Maintenance"), ("food_beverage", "Food & Beverage")]


def load_rooms(path: str) -> list[dict]:
    rooms = json.loads(open(path).read())
    if not isinstance(rooms, list) or not rooms:
        raise ValidationFailed("rooms file must be a non-empty JSON list")
    seen = set()
    for r in rooms:
        for k in ("code", "name", "max_occupancy", "base_rate", "count"):
            if k not in r:
                raise ValidationFailed(f"room type missing '{k}': {r}")
        if r["code"] in seen:
            raise ValidationFailed(f"duplicate room type code {r['code']}")
        seen.add(r["code"])
        if int(r["count"]) < 1 or int(r["max_occupancy"]) < 1 or Decimal(str(r["base_rate"])) <= 0:
            raise ValidationFailed(f"room type {r['code']} needs positive count/occupancy/rate")
    return rooms


def extend_inventory(db, hotel: Hotel, days: int, start: date | None = None) -> int:
    """Create missing RoomInventory rows for the next `days` nights. Existing rows are untouched
    (bookings and rate changes survive). Returns the number of nights added."""
    start = start or date.today()
    added = 0
    for rt in db.scalars(select(RoomType).where(RoomType.hotel_id == hotel.id)):
        count = len(db.scalars(select(Room.id).where(Room.room_type_id == rt.id)).all())
        have = set(db.scalars(select(RoomInventory.night).where(
            RoomInventory.room_type_id == rt.id, RoomInventory.night >= start)))
        for d in range(days):
            night = start + timedelta(days=d)
            if night not in have:
                db.add(RoomInventory(organization_id=hotel.organization_id, hotel_id=hotel.id,
                                     room_type_id=rt.id, night=night, total=count, booked=0,
                                     rate=rt.base_rate))
                added += 1
    db.commit()
    return added


def bootstrap(*, org_name, org_slug, hotel_name, hotel_slug, admin_email, admin_name,
              admin_password, rooms, emergency_instructions, front_desk_phone="",
              timezone="UTC", currency="USD", inventory_days=365) -> dict:
    settings = get_settings()
    validate_password(admin_password, email=admin_email, full_name=admin_name,
                      min_length=settings.password_min_length)
    if not emergency_instructions.strip():
        raise ValidationFailed("emergency instructions are required: guests are shown them in an emergency")
    with SessionLocal() as db:
        if db.scalar(select(Organization.id).where(Organization.slug == org_slug)):
            raise ValidationFailed(f"organization '{org_slug}' already exists; nothing was changed")
        org = Organization(name=org_name, slug=org_slug)
        db.add(org)
        db.flush()
        hotel = Hotel(organization_id=org.id, name=hotel_name, slug=hotel_slug, timezone=timezone,
                      currency=currency, settings={"emergency_instructions": emergency_instructions,
                                                   "front_desk_phone": front_desk_phone})
        db.add(hotel)
        db.flush()
        for code, name in DEPARTMENTS:
            db.add(Department(organization_id=org.id, hotel_id=hotel.id, code=code, name=name))
        next_on_floor: dict[int, int] = {}  # room numbers are unique per hotel across room types
        for rt_spec in rooms:
            rt = RoomType(organization_id=org.id, hotel_id=hotel.id, code=rt_spec["code"],
                          name=rt_spec["name"], max_occupancy=int(rt_spec["max_occupancy"]),
                          base_rate=Decimal(str(rt_spec["base_rate"])),
                          description=rt_spec.get("description", ""))
            db.add(rt)
            db.flush()
            floor = int(rt_spec.get("floor", 1))
            for _ in range(int(rt_spec["count"])):
                n = next_on_floor[floor] = next_on_floor.get(floor, 0) + 1
                db.add(Room(organization_id=org.id, hotel_id=hotel.id, room_type_id=rt.id,
                            number=f"{floor}{n:02d}", floor=floor))
        admin = User(organization_id=org.id, email=admin_email.lower(), full_name=admin_name,
                     password_hash=hash_password(admin_password), role=Role.admin)
        db.add(admin)
        db.flush()
        db.add(StaffAssignment(organization_id=org.id, user_id=admin.id, hotel_id=hotel.id,
                               department_id=None))
        db.commit()
        nights = extend_inventory(db, hotel, inventory_days)
        return {"organization": org_slug, "hotel": hotel_slug, "admin": admin.email,
                "room_types": len(rooms), "inventory_nights_added": nights}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    ext = sub.add_parser("extend-inventory")
    ext.add_argument("--hotel-slug", required=True)
    ext.add_argument("--days", type=int, default=365)
    for a in ("org-name", "org-slug", "hotel-name", "hotel-slug", "admin-email", "admin-name",
              "emergency-instructions"):
        p.add_argument(f"--{a}")
    p.add_argument("--rooms", help="path to rooms.json")
    p.add_argument("--front-desk-phone", default="")
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--currency", default="USD")
    p.add_argument("--inventory-days", type=int, default=365)
    a = p.parse_args(argv)
    if a.cmd == "extend-inventory":
        with SessionLocal() as db:
            hotels = db.scalars(select(Hotel).where(Hotel.slug == a.hotel_slug)).all()
            if len(hotels) != 1:
                print(f"hotel slug '{a.hotel_slug}' matched {len(hotels)} hotels; need exactly one",
                      file=sys.stderr)
                return 2
            print(f"added {extend_inventory(db, hotels[0], a.days)} nights")
        return 0
    missing = [n for n in ("org_name", "org_slug", "hotel_name", "hotel_slug", "admin_email",
                           "admin_name", "emergency_instructions", "rooms") if not getattr(a, n)]
    if missing:
        p.error("missing: " + ", ".join("--" + m.replace("_", "-") for m in missing))
    pw = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    try:
        out = bootstrap(org_name=a.org_name, org_slug=a.org_slug, hotel_name=a.hotel_name,
                        hotel_slug=a.hotel_slug, admin_email=a.admin_email, admin_name=a.admin_name,
                        admin_password=pw, rooms=load_rooms(a.rooms),
                        emergency_instructions=a.emergency_instructions,
                        front_desk_phone=a.front_desk_phone, timezone=a.timezone,
                        currency=a.currency, inventory_days=a.inventory_days)
    except ValidationFailed as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
