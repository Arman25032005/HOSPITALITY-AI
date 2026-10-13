from tests.conftest import HOTEL_ID, DEMO_PASSWORD, stay

BASE = f"/api/v1/hotels/{HOTEL_ID}"


def login_other(client, other_org):
    r = client.post("/api/v1/auth/login",
                    json={"email": other_org["email"], "password": DEMO_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_other_tenant_cannot_see_hotel_resources(client, auth, other_org, guest_id):
    rec = auth("reception@demo.hotel")
    tid = client.post(f"{BASE}/tickets", json={"category": "maintenance", "description": "x"},
                      headers=rec).json()["id"]
    other = login_other(client, other_org)
    for method, path in [
        ("get", f"{BASE}/tickets"), ("get", f"{BASE}/tickets/{tid}"),
        ("get", f"{BASE}/availability"),
    ]:
        r = getattr(client, method)(path, params={**stay(), "adults": 1}, headers=other)
        assert r.status_code == 404, (path, r.status_code)
    r = client.post(f"{BASE}/guests", json={"full_name": "Intruder"}, headers=other)
    assert r.status_code == 404


def test_other_tenant_cannot_use_foreign_guest_in_own_hotel(client, other_org, guest_id):
    other = login_other(client, other_org)
    body = {**stay(), "adults": 1, "guest_id": str(guest_id),
            "room_type_id": "00000000-0000-0000-0000-000000000000"}
    r = client.post(f"/api/v1/hotels/{other_org['hotel_id']}/reservations", json=body,
                    headers=other)
    assert r.status_code == 404  # guest / room type not found in caller's tenant


def test_requires_authentication(client):
    assert client.get(f"{BASE}/tickets").status_code == 401
    assert client.post(f"{BASE}/reservations", json={}).status_code == 401
