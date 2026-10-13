from scripts.seed import DEMO_PASSWORD


def login(client, email, password=DEMO_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_login_and_me(client, seeded):
    r = login(client, "manager@demo.hotel")
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "manager"


def test_bad_password_rejected(client, seeded):
    assert login(client, "manager@demo.hotel", "wrong").status_code == 401


def test_unknown_user_rejected(client, seeded):
    assert login(client, "nobody@demo.hotel").status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_garbage_token(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
