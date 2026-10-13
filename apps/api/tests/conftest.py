import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def schema():
    import app.models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def seeded(db):
    from scripts.seed import seed

    seed()
    yield
    db.execute(text("TRUNCATE organizations CASCADE"))
    db.commit()


# ---- helpers -------------------------------------------------------------
import uuid  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from scripts.seed import DEMO_PASSWORD, uid  # noqa: E402

HOTEL_ID = uid("hotel")


def stay(offset: int = 10, nights: int = 2) -> dict:
    ci = date.today() + timedelta(days=offset)
    return {"check_in": ci.isoformat(), "check_out": (ci + timedelta(days=nights)).isoformat()}


@pytest.fixture()
def auth(client, seeded):
    def _auth(email: str) -> dict:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _auth


@pytest.fixture()
def guest_id(client, auth):
    r = client.post(f"/api/v1/hotels/{HOTEL_ID}/guests", json={"full_name": "Test Guest"},
                    headers=auth("reception@demo.hotel"))
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


@pytest.fixture()
def other_org(db):
    """A second tenant with its own hotel and manager (password: DEMO_PASSWORD)."""
    from app.core.security import hash_password
    from app.models import Hotel, Organization, Role, User

    org = Organization(name="Other Group", slug="other-group")
    db.add(org)
    db.flush()
    hotel = Hotel(organization_id=org.id, name="Other Hotel", slug="other-hotel")
    user = User(organization_id=org.id, email="manager@other.hotel", full_name="Other Manager",
                password_hash=hash_password(DEMO_PASSWORD), role=Role.manager)
    db.add_all([hotel, user])
    db.commit()
    return {"org_id": org.id, "hotel_id": hotel.id, "email": user.email}
