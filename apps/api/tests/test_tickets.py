from sqlalchemy import select

from app.models import AuditEvent, User
from tests.conftest import HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}"


def make(client, headers, category="maintenance", **kw):
    body = {"category": category, "description": "AC is broken", "room_number": "205", **kw}
    return client.post(f"{BASE}/tickets", json=body, headers=headers)


def test_create_routes_to_department_and_persists(client, auth, db):
    r = make(client, auth("reception@demo.hotel"), priority="high")
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["ticket_number"].startswith("T-") and t["status"] == "open"
    assert t["department_id"] is not None and t["due_at"] is not None
    again = client.get(f"{BASE}/tickets/{t['id']}", headers=auth("manager@demo.hotel"))
    assert again.json()["ticket_number"] == t["ticket_number"]
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "ticket.created"))


def test_unknown_category_rejected(client, auth):
    assert make(client, auth("reception@demo.hotel"), category="teleport").status_code == 422


def test_status_workflow_and_invalid_transition(client, auth):
    h = auth("reception@demo.hotel")
    tid = make(client, h).json()["id"]
    bad = client.post(f"{BASE}/tickets/{tid}/status", json={"status": "resolved"}, headers=h)
    assert bad.status_code == 409
    for s in ("in_progress", "resolved"):
        r = client.post(f"{BASE}/tickets/{tid}/status", json={"status": s}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == s
    assert r.json()["resolved_at"] is not None
    closed = client.post(f"{BASE}/tickets/{tid}/status", json={"status": "closed"}, headers=h)
    assert closed.json()["status"] == "closed"
    reopen = client.post(f"{BASE}/tickets/{tid}/status", json={"status": "open"}, headers=h)
    assert reopen.status_code == 409


def test_department_staff_sees_only_own_department(client, auth):
    rec = auth("reception@demo.hotel")
    maint = make(client, rec, "maintenance").json()["id"]
    hk = make(client, rec, "housekeeping").json()["id"]
    mh = auth("maintenance@demo.hotel")
    ids = {t["id"] for t in client.get(f"{BASE}/tickets", headers=mh).json()}
    assert ids == {maint}
    assert client.get(f"{BASE}/tickets/{hk}", headers=mh).status_code == 404
    ok = client.post(f"{BASE}/tickets/{maint}/status", json={"status": "in_progress"}, headers=mh)
    assert ok.status_code == 200
    no = client.post(f"{BASE}/tickets/{hk}/status", json={"status": "in_progress"}, headers=mh)
    assert no.status_code == 404


def test_assign_and_notes(client, auth, db):
    rec = auth("reception@demo.hotel")
    tid = make(client, rec).json()["id"]
    assignee = db.scalar(select(User).where(User.email == "maintenance@demo.hotel"))
    r = client.post(f"{BASE}/tickets/{tid}/assign", json={"assignee_id": str(assignee.id)},
                    headers=rec)
    assert r.json()["status"] == "assigned" and r.json()["assigned_to"] == str(assignee.id)
    n = client.post(f"{BASE}/tickets/{tid}/notes", json={"note": "Parts ordered"}, headers=rec)
    assert n.json()["internal_notes"][0]["text"] == "Parts ordered"
    denied = client.post(f"{BASE}/tickets/{tid}/assign", json={"assignee_id": str(assignee.id)},
                         headers=auth("maintenance@demo.hotel"))
    assert denied.status_code == 403


def test_filters(client, auth):
    rec = auth("reception@demo.hotel")
    make(client, rec, priority="urgent")
    make(client, rec, priority="low")
    r = client.get(f"{BASE}/tickets", params={"priority": "urgent"}, headers=rec)
    assert [t["priority"] for t in r.json()] == ["urgent"]
