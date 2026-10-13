"""Deterministic demo seed. Idempotent: safe to run repeatedly. DEMO DATA ONLY."""
import uuid
from datetime import date, timedelta
from decimal import Decimal


from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import (
    Department, Hotel, Organization, Role, Room, RoomInventory, RoomType, StaffAssignment, User,
)

NS = uuid.UUID("6f1c1e0a-0000-4000-8000-000000000001")


def uid(name: str) -> uuid.UUID:
    return uuid.uuid5(NS, name)


DEMO_PASSWORD = "demo-password-123"  # demo only; never use outside local development
ROOM_TYPES = [
    ("standard", "Standard Queen", 2, "150.00", 6),
    ("deluxe", "Deluxe King", 3, "220.00", 4),
    ("suite", "Executive Suite", 4, "380.00", 2),
]
DEPARTMENTS = [("front_desk", "Front Desk"), ("housekeeping", "Housekeeping"),
               ("maintenance", "Maintenance"), ("food_beverage", "Food & Beverage")]
USERS = [
    ("admin@demo.hotel", "Dana Admin", Role.admin, None),
    ("manager@demo.hotel", "Morgan Manager", Role.manager, None),
    ("reception@demo.hotel", "Riley Reception", Role.receptionist, "front_desk"),
    ("housekeeping@demo.hotel", "Hayden Housekeeping", Role.department_staff, "housekeeping"),
    ("maintenance@demo.hotel", "Max Maintenance", Role.department_staff, "maintenance"),
]


def get_or_create(db, model, id_, **fields):
    obj = db.get(model, id_)
    if obj is None:
        obj = model(id=id_, **fields)
        db.add(obj)
    return obj


def seed() -> None:
    with SessionLocal() as db:
        org = get_or_create(db, Organization, uid("org"), name="Demo Hotel Group", slug="demo-group")
        db.flush()
        hotel = get_or_create(db, Hotel, uid("hotel"), organization_id=org.id,
                              name="Demo Grand Hotel", slug="demo-grand", timezone="UTC")
        db.flush()
        depts = {}
        for code, name in DEPARTMENTS:
            depts[code] = get_or_create(db, Department, uid(f"dept-{code}"),
                                        organization_id=org.id, hotel_id=hotel.id,
                                        code=code, name=name)
        pw = hash_password(DEMO_PASSWORD)
        for email, name, role, dept in USERS:
            u = get_or_create(db, User, uid(f"user-{email}"), organization_id=org.id,
                              email=email, full_name=name, password_hash=pw, role=role)
            db.flush()
            get_or_create(db, StaffAssignment, uid(f"assign-{email}"), organization_id=org.id,
                          user_id=u.id, hotel_id=hotel.id,
                          department_id=depts[dept].id if dept else None)
        today = date(2026, 10, 1)  # fixed anchor for determinism
        for code, name, occ, rate, count in ROOM_TYPES:
            rt = get_or_create(db, RoomType, uid(f"rt-{code}"), organization_id=org.id,
                               hotel_id=hotel.id, code=code, name=name, max_occupancy=occ,
                               base_rate=Decimal(rate), description=f"Demo {name}")
            db.flush()
            for n in range(count):
                get_or_create(db, Room, uid(f"room-{code}-{n}"), organization_id=org.id,
                              hotel_id=hotel.id, room_type_id=rt.id,
                              number=f"{ {'standard':1,'deluxe':2,'suite':3}[code] }{n + 1:02d}",
                              floor={"standard": 1, "deluxe": 2, "suite": 3}[code])
            for d in range(365):
                night = today + timedelta(days=d)
                get_or_create(db, RoomInventory, uid(f"inv-{code}-{night}"),
                              organization_id=org.id, hotel_id=hotel.id, room_type_id=rt.id,
                              night=night, total=count, booked=0, rate=Decimal(rate))
        db.commit()
    print("Seed complete (demo data). Staff password:", DEMO_PASSWORD)


if __name__ == "__main__":
    seed()
