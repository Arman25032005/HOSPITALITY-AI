from sqlalchemy import select

from app.models import User
from tests.conftest import DEMO_PASSWORD, HOTEL_ID


def test_hotel_list_follows_role_and_tenant(client, auth, other_org):
    for email in ("manager@demo.hotel", "reception@demo.hotel", "housekeeping@demo.hotel"):
        rows = client.get("/api/v1/hotels", headers=auth(email)).json()
        assert [r["id"] for r in rows] == [str(HOTEL_ID)]
    tok = client.post("/api/v1/auth/login", json={"email": other_org["email"],
                                                  "password": DEMO_PASSWORD}).json()["access_token"]
    rows = client.get("/api/v1/hotels", headers={"Authorization": f"Bearer {tok}"}).json()
    assert [r["id"] for r in rows] == [str(other_org["hotel_id"])]
    assert client.get("/api/v1/hotels").status_code == 401


def test_unassigned_staff_see_no_hotels(client, auth, db):
    from app.models import StaffAssignment
    rec = db.scalar(select(User).where(User.email == "reception@demo.hotel"))
    for a in db.scalars(select(StaffAssignment).where(StaffAssignment.user_id == rec.id)):
        db.delete(a)
    db.commit()
    assert client.get("/api/v1/hotels", headers=auth("reception@demo.hotel")).json() == []


def test_refresh_issues_working_token_and_rejects_deactivated_users(client, auth, db):
    h = auth("manager@demo.hotel")
    new = client.post("/api/v1/auth/refresh", headers=h).json()["access_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert client.post("/api/v1/auth/refresh").status_code == 401
    guest = client.post("/api/v1/chat/sessions", json={
        "org_slug": "demo-group", "hotel_slug": "demo-grand"}).json()["token"]
    assert client.post("/api/v1/auth/refresh", headers={
        "Authorization": f"Bearer {guest}"}).status_code == 401  # guest tokens can't become staff
    user = db.scalar(select(User).where(User.email == "manager@demo.hotel"))
    user.is_active = False
    db.commit()
    assert client.post("/api/v1/auth/refresh", headers=h).status_code == 401
