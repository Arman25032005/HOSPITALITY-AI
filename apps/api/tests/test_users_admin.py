import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import AuditEvent, Hotel, Organization, Role, User
from scripts.seed import uid
from tests.conftest import DEMO_PASSWORD, HOTEL_ID

GOOD = "Correct-Horse-9-Battery"


def hdr(client, email, pw=DEMO_PASSWORD):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def new_user(client, admin, **kw):
    body = {"email": "casey@demo.hotel", "full_name": "Casey Clerk", "role": "receptionist",
            "password": GOOD, "assignments": [{"hotel_id": str(HOTEL_ID)}], **kw}
    return client.post("/api/v1/users", json=body, headers=admin)


def test_only_admins_manage_users(client, auth):
    for email in ("manager@demo.hotel", "reception@demo.hotel", "housekeeping@demo.hotel"):
        h = auth(email)
        assert client.get("/api/v1/users", headers=h).status_code == 403
        assert new_user(client, h).status_code == 403
    assert client.get("/api/v1/users").status_code == 401


def test_list_has_no_secrets_and_shows_assignments(client, auth):
    rows = client.get("/api/v1/users", headers=auth("admin@demo.hotel")).json()
    assert len(rows) == 5 and "password" not in str(rows).lower()
    hk = next(r for r in rows if r["email"] == "housekeeping@demo.hotel")
    assert hk["assignments"][0]["department_name"] == "Housekeeping"
    assert hk["assignments"][0]["hotel_name"] == "Demo Grand Hotel"


def test_create_user_can_sign_in_and_is_audited_without_the_password(client, auth, db):
    admin = auth("admin@demo.hotel")
    r = new_user(client, admin)
    assert r.status_code == 201 and r.json()["role"] == "receptionist"
    h = hdr(client, "casey@demo.hotel", GOOD)
    assert client.get("/api/v1/hotels", headers=h).json()[0]["id"] == str(HOTEL_ID)
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "user.created"))
    assert ev.details == {"role": "receptionist"} and GOOD not in str(ev.details)
    assert new_user(client, admin).status_code == 409                         # duplicate email
    assert new_user(client, admin, email="CASEY@demo.hotel").status_code == 409


@pytest.mark.parametrize("pw,why", [
    ("short1!", "at least"), ("password123456", "too easy"), ("aaaaaaaaaaaa", "too easy"),
    ("casey-clerk-2026", "name or email"), ("onlylettersnodigits", "mix"), ("908172635491", "mix"),
])
def test_password_policy(client, auth, pw, why):
    r = new_user(client, auth("admin@demo.hotel"), password=pw)
    assert r.status_code == 422 and why in r.json()["detail"]


def test_assignments_are_validated_against_the_tenant(client, auth, other_org):
    admin = auth("admin@demo.hotel")
    foreign = new_user(client, admin, assignments=[{"hotel_id": str(other_org["hotel_id"])}])
    assert foreign.status_code == 404
    bad_dept = new_user(client, admin, assignments=[{"hotel_id": str(HOTEL_ID),
                                                     "department_id": str(uid("rt-suite"))}])
    assert bad_dept.status_code == 404
    assert client.get("/api/v1/users", headers=admin).json().__len__() == 5  # nothing half-created


def test_role_change_and_deactivation_revoke_existing_sessions(client, auth):
    admin = auth("admin@demo.hotel")
    victim = auth("reception@demo.hotel")
    assert client.get("/api/v1/auth/me", headers=victim).status_code == 200
    users = {u["email"]: u for u in client.get("/api/v1/users", headers=admin).json()}
    uid_ = users["reception@demo.hotel"]["id"]
    assert client.patch(f"/api/v1/users/{uid_}", json={"role": "department_staff"},
                        headers=admin).json()["role"] == "department_staff"
    assert client.get("/api/v1/auth/me", headers=victim).status_code == 401  # old token dead
    fresh = hdr(client, "reception@demo.hotel")
    assert client.get("/api/v1/auth/me", headers=fresh).json()["role"] == "department_staff"
    client.patch(f"/api/v1/users/{uid_}", json={"is_active": False}, headers=admin)
    assert client.get("/api/v1/auth/me", headers=fresh).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "reception@demo.hotel",
                                                   "password": DEMO_PASSWORD}).status_code == 401
    client.patch(f"/api/v1/users/{uid_}", json={"is_active": True}, headers=admin)
    assert client.post("/api/v1/auth/login", json={"email": "reception@demo.hotel",
                                                   "password": DEMO_PASSWORD}).status_code == 200


def test_admin_guard_rails(client, auth, db):
    admin = auth("admin@demo.hotel")
    users = {u["email"]: u for u in client.get("/api/v1/users", headers=admin).json()}
    me = users["admin@demo.hotel"]["id"]
    assert client.patch(f"/api/v1/users/{me}", json={"role": "manager"}, headers=admin).status_code == 403
    assert client.patch(f"/api/v1/users/{me}", json={"is_active": False}, headers=admin).status_code == 403
    # promote a second admin, then the original cannot be the last one standing by accident
    mgr = users["manager@demo.hotel"]["id"]
    client.patch(f"/api/v1/users/{mgr}", json={"role": "admin"}, headers=admin)
    second = hdr(client, "manager@demo.hotel")
    assert client.patch(f"/api/v1/users/{me}", json={"is_active": False}, headers=second).status_code == 200
    assert client.patch(f"/api/v1/users/{mgr}", json={"role": "manager"}, headers=second).status_code == 403
    only = db.scalars(select(User).where(User.role == Role.admin, User.is_active.is_(True))).all()
    assert len(only) == 1
    third = new_user(client, second, role="admin").json()["id"]
    client.patch(f"/api/v1/users/{third}", json={"is_active": False}, headers=second)
    assert client.patch(f"/api/v1/users/{mgr}", json={"is_active": False},
                        headers=second).status_code == 403  # self


def test_password_reset_and_self_service_change(client, auth, db):
    admin = auth("admin@demo.hotel")
    victim = auth("housekeeping@demo.hotel")
    uid_ = next(u["id"] for u in client.get("/api/v1/users", headers=admin).json()
                if u["email"] == "housekeeping@demo.hotel")
    assert client.post(f"/api/v1/users/{uid_}/reset-password", json={"new_password": "short"},
                       headers=admin).status_code == 422
    assert client.post(f"/api/v1/users/{uid_}/reset-password", json={"new_password": GOOD},
                       headers=admin).status_code == 204
    assert client.get("/api/v1/auth/me", headers=victim).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "housekeeping@demo.hotel",
                                                   "password": DEMO_PASSWORD}).status_code == 401
    h = hdr(client, "housekeeping@demo.hotel", GOOD)
    new_pw = "Another-Strong-Pass-77"
    assert client.post("/api/v1/auth/change-password", headers=h, json={
        "current_password": "wrong", "new_password": new_pw}).status_code == 403
    assert client.post("/api/v1/auth/change-password", headers=h, json={
        "current_password": GOOD, "new_password": GOOD}).status_code == 422
    changed = client.post("/api/v1/auth/change-password", headers=h, json={
        "current_password": GOOD, "new_password": new_pw})
    assert changed.status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401           # old session revoked
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {changed.json()['access_token']}"}
                      ).status_code == 200
    actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"user.password_reset", "user.password_changed"} <= actions
    assert all(GOOD not in str(e.details) and new_pw not in str(e.details)
               for e in db.scalars(select(AuditEvent)))


def test_user_admin_is_tenant_scoped(client, auth, db, other_org):
    other = db.get(Organization, other_org["org_id"])
    boss = User(organization_id=other.id, email="boss@other.hotel", full_name="Other Boss",
                password_hash=hash_password(DEMO_PASSWORD), role=Role.admin)
    db.add(boss)
    db.commit()
    other_admin = hdr(client, "boss@other.hotel")
    ours = next(u["id"] for u in client.get("/api/v1/users", headers=auth("admin@demo.hotel")).json()
                if u["email"] == "manager@demo.hotel")
    assert client.patch(f"/api/v1/users/{ours}", json={"role": "receptionist"},
                        headers=other_admin).status_code == 404
    assert client.post(f"/api/v1/users/{ours}/reset-password", json={"new_password": GOOD},
                       headers=other_admin).status_code == 404
    listed = client.get("/api/v1/users", headers=other_admin).json()
    assert {u["email"] for u in listed} == {"manager@other.hotel", "boss@other.hotel"}
    assert db.get(Hotel, HOTEL_ID) is not None
