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


# ---- chat helpers --------------------------------------------------------
import itertools  # noqa: E402

from app.api.routes.chat import get_llm_dep  # noqa: E402
from app.api.routes.webhooks import get_senders  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.conversations.ratelimit import limiter  # noqa: E402

_ids = itertools.count(1)


@pytest.fixture(autouse=True)
def _reset_limiter_and_overrides():
    from app.rag.cache import answer_cache

    limiter.reset()
    answer_cache.clear()
    yield
    app.dependency_overrides.pop(get_llm_dep, None)
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_senders, None)


@pytest.fixture()
def use_llm():
    def _use(llm):
        app.dependency_overrides[get_llm_dep] = lambda: llm
        return llm

    return _use


@pytest.fixture()
def use_settings():
    def _use(**kw):
        s = get_settings().model_copy(update=kw)
        app.dependency_overrides[get_settings] = lambda: s
        return s

    return _use


class Chat:
    def __init__(self, client, org_slug="demo-group", hotel_slug="demo-grand"):
        self.client = client
        r = client.post("/api/v1/chat/sessions", json={"org_slug": org_slug,
                                                       "hotel_slug": hotel_slug})
        assert r.status_code == 201, r.text
        self.headers = {"Authorization": f"Bearer {r.json()['token']}"}
        self.conversation_id = r.json()["conversation_id"]

    def say(self, text, message_id=None, expect=200):
        r = self.client.post("/api/v1/chat/messages", headers=self.headers, json={
            "text": text, "client_message_id": message_id or f"m{next(_ids)}"})
        assert r.status_code == expect, r.text
        return r.json()

    def history(self):
        return self.client.get("/api/v1/chat/messages", headers=self.headers).json()

    def verify(self, code, last_name):
        return self.client.post("/api/v1/chat/verify", headers=self.headers, json={
            "confirmation_code": code, "last_name": last_name}).json()


@pytest.fixture()
def chat(client, seeded):
    return lambda **kw: Chat(client, **kw)


@pytest.fixture()
def in_house(client, auth):
    """Create + check in a reservation for 'Jane Doe' arriving today; returns (code, id)."""
    rec = auth("reception@demo.hotel")
    base = f"/api/v1/hotels/{HOTEL_ID}"
    guest = client.post(f"{base}/guests", json={"full_name": "Jane Doe"}, headers=rec).json()
    ci = date.today()
    res = client.post(f"{base}/reservations", headers=rec, json={
        "check_in": ci.isoformat(), "check_out": (ci + timedelta(days=2)).isoformat(),
        "adults": 2, "guest_id": guest["id"], "room_type_id": str(uid("rt-deluxe"))})
    assert res.status_code == 201, res.text
    r = client.post(f"{base}/reservations/{res.json()['id']}/check-in", headers=rec, json={})
    assert r.status_code == 200, r.text
    return res.json()["confirmation_code"], res.json()["id"]


# ---- channel helpers -----------------------------------------------------
import types  # noqa: E402

from app.channels.mock import MockSender  # noqa: E402
from app.channels.twilio import compute_signature  # noqa: E402

WA_NUMBER = "whatsapp:+14155550100"
WA_URL = "https://hotel.example/api/v1/webhooks/twilio/whatsapp"


@pytest.fixture()
def channels(client, auth, use_settings, seeded):
    settings = use_settings(
        public_base_url="https://hotel.example", twilio_account_sid="AC" + "0" * 32,
        twilio_auth_token="tok-secret", twilio_whatsapp_number=WA_NUMBER,
        telegram_bot_token="123:abc", telegram_webhook_secret="tg-secret")
    wa, tg = MockSender("twilio_whatsapp"), MockSender("telegram", 4000)
    app.dependency_overrides[get_senders] = lambda: (lambda ch: {"whatsapp": wa,
                                                                 "telegram": tg}.get(ch))
    admin = auth("admin@demo.hotel")
    r1 = client.put(f"/api/v1/hotels/{HOTEL_ID}/integrations/twilio_whatsapp", headers=admin,
                    json={"enabled": True, "config": {"number": WA_NUMBER}})
    r2 = client.put(f"/api/v1/hotels/{HOTEL_ID}/integrations/telegram", headers=admin,
                    json={"enabled": True, "config": {}})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    ctx = types.SimpleNamespace(settings=settings, wa=wa, tg=tg, tg_id=r2.json()["id"],
                                client=client, counter=itertools.count(1))

    def wa_send(body, frm="whatsapp:+14155551234", sid=None, media=0, sign=True, to=WA_NUMBER,
                extra=None):
        sid = sid or "SM" + f"{next(ctx.counter):032x}"
        params = {"MessageSid": sid, "From": frm, "To": to, "Body": body, "NumMedia": str(media),
                  "AccountSid": "AC" + "0" * 32, **(extra or {})}
        headers = {}
        if sign:
            headers["X-Twilio-Signature"] = compute_signature(
                "tok-secret", WA_URL, {k: [v] for k, v in params.items()})
        return client.post("/api/v1/webhooks/twilio/whatsapp", data=params, headers=headers)

    def tg_send(text=None, chat=777, update_id=None, secret="tg-secret", extra=None, raw=None):
        uid_ = update_id if update_id is not None else next(ctx.counter) + 1000
        payload = raw or {"update_id": uid_, "message": {
            "message_id": uid_, "chat": {"id": chat, "type": "private"}, "text": text,
            **(extra or {})}}
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
        return client.post(f"/api/v1/webhooks/telegram/{ctx.tg_id}", json=payload, headers=headers)

    ctx.wa_send, ctx.tg_send = wa_send, tg_send
    return ctx
