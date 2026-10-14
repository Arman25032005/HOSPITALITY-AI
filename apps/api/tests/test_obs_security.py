import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select

from app.config import Settings
from app.core import tracing
from app.core.http_security import SecurityMiddleware
from app.core.observability import JsonFormatter, PiiFilter, request_id_var
from app.core.secrets import load_secret_overrides
from app.models import AuditEvent, User
from app.rag.embeddings import ConfigError
from tests.conftest import DEMO_PASSWORD, HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}"


def metric(client, name_and_labels: str) -> float:
    text = client.get("/metrics").text
    m = re.search(rf"^{re.escape(name_and_labels)} ([0-9.e+-]+)$", text, re.M)
    return float(m.group(1)) if m else 0.0


# ------------------------------------------------------------------ metrics

def test_metrics_endpoint_is_protected_when_a_token_is_configured(client, use_settings):
    use_settings(metrics_token="scrape-secret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert ok.status_code == 200 and "http_requests_total" in ok.text


def test_domain_metrics_increment_and_labels_stay_low_cardinality(client, chat, auth, in_house, db):
    key = 'agent_turns_total{intent="knowledge",outcome="answered"}'
    tool = 'tool_calls_total{result="ok",tool="search_knowledge"}'
    before, tool_before = metric(client, key), metric(client, tool)
    chat().say("What time is check-out?")
    assert metric(client, key) == before + 1
    assert metric(client, tool) >= tool_before + 1
    c = chat()
    c.verify(in_house[0], "Doe")
    t0 = metric(client, 'tickets_created_total{category="maintenance"}')
    c.say("my TV is not working")
    assert metric(client, 'tickets_created_total{category="maintenance"}') == t0 + 1
    rec = auth("reception@demo.hotel")
    client.get(f"{BASE}/tickets", headers=rec)
    text = client.get("/metrics").text
    assert "{hotel_id}" in text                       # route templates, not concrete paths
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", text)  # no ids in labels
    assert "Doe" not in text and "TV" not in text      # no guest content


def test_webhook_and_outbound_metrics(channels, client):
    d0 = metric(client, 'webhook_events_total{provider="telegram",result="duplicate"}')
    p0 = metric(client, 'webhook_events_total{provider="telegram",result="processed"}')
    channels.tg_send("What time is check-out?", update_id=777001)
    channels.tg_send("What time is check-out?", update_id=777001)
    assert metric(client, 'webhook_events_total{provider="telegram",result="processed"}') == p0 + 1
    assert metric(client, 'webhook_events_total{provider="telegram",result="duplicate"}') == d0 + 1
    assert metric(client, 'outbound_messages_total{channel="telegram",result="sent"}') >= 1


# ------------------------------------------------------------------ request ids and logs

def test_request_id_is_generated_echoed_and_sanitised(client):
    r = client.get("/health")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["x-request-id"])
    ok = client.get("/health", headers={"X-Request-ID": "trace-abc-12345"})
    assert ok.headers["x-request-id"] == "trace-abc-12345"
    bad = client.get("/health", headers={"X-Request-ID": "x\r\nSet-Cookie: a=b"})
    assert "set-cookie" not in bad.headers and re.fullmatch(r"[0-9a-f]{32}", bad.headers["x-request-id"])


def test_json_logs_carry_request_id_extras_and_redact_pii():
    rec = logging.LogRecord("hospitality.test", logging.INFO, __file__, 1,
                            "Guest jane@example.com called +1 415 555 0100 card 4111 1111 1111 1111",
                            (), None)
    rec.route, rec.status = "/x", 200
    PiiFilter().filter(rec)
    token = request_id_var.set("req-123456789")
    try:
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["request_id"] == "req-123456789" and out["route"] == "/x" and out["level"] == "INFO"
    for secret in ("jane@example.com", "415 555 0100", "4111"):
        assert secret not in out["msg"]


def test_exception_logs_record_type_and_location_but_not_the_message():
    try:
        raise ValueError("secret guest@example.com in message")
    except ValueError:
        import sys
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert out["exc_type"] == "ValueError" and "guest@example.com" not in json.dumps(out)
    assert out["exc_at"].startswith("test_obs_security.py:")


# ------------------------------------------------------------------ tracing

@pytest.fixture()
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "tracer", provider.get_tracer("test"))
    return exporter


def test_agent_turn_and_tool_spans_have_no_guest_text(spans, chat):
    chat().say("What time is check-out? secret.guest@example.com")
    names = [s.name for s in spans.get_finished_spans()]
    assert "agent.turn" in names and "tool.search_knowledge" in names
    turn = next(s for s in spans.get_finished_spans() if s.name == "agent.turn")
    assert turn.attributes["agent.intent"] == "knowledge" and turn.attributes["agent.outcome"]
    assert "secret.guest" not in json.dumps({s.name: dict(s.attributes) for s in spans.get_finished_spans()})


def test_tracing_is_a_noop_without_an_endpoint_and_instruments_http_when_enabled():
    assert tracing.setup_tracing(FastAPI(), Settings(otel_exporter_otlp_endpoint="")) is None
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    exporter = InMemorySpanExporter()
    original = tracing.tracer
    try:
        assert tracing.setup_tracing(app, Settings(), exporter=exporter) is not None
        TestClient(app).get("/ping")
        assert any("/ping" in s.name for s in exporter.get_finished_spans())
    finally:
        tracing.tracer = original


# ------------------------------------------------------------------ security headers and size limits

def test_security_headers_on_api_responses(client):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    assert "default-src 'none'" in h["content-security-policy"]
    assert "strict-transport-security" not in h  # only in production


def test_hsts_only_in_production():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    def build(prod):
        wrapped = FastAPI()
        wrapped.add_middleware(SecurityMiddleware, max_bytes=10, upload_max_bytes=20, production=prod)
        wrapped.mount("/", app)  # type: ignore[arg-type]
        return TestClient(wrapped)

    assert "strict-transport-security" in build(True).get("/").headers
    assert "strict-transport-security" not in build(False).get("/").headers


def test_oversized_bodies_are_rejected_before_processing(client, auth, chat):
    c = chat()
    big = client.post("/api/v1/chat/messages", headers=c.headers,
                      content=b"x" * 1_100_000, )
    assert big.status_code == 413 and big.json()["detail"] == "Request body too large"
    streamed = client.post("/api/v1/chat/messages", headers=c.headers,
                           content=iter([b"x" * 600_000, b"x" * 600_000]))
    assert streamed.status_code == 413
    upload = client.post(f"{BASE}/knowledge/documents", headers=auth("manager@demo.hotel"),
                         data={"title": "T", "doc_type": "policy"},
                         files={"file": ("a.md", b"x" * 6_000_000)})
    assert upload.status_code == 413
    ok = client.post("/api/v1/chat/messages", headers=c.headers, json={
        "text": "What time is check-out?", "client_message_id": "size-ok"})
    assert ok.status_code == 200


# ------------------------------------------------------------------ login throttling

def login(client, email="manager@demo.hotel", pw=DEMO_PASSWORD, headers=None):
    return client.post("/api/v1/auth/login", json={"email": email, "password": pw}, headers=headers)


def test_login_is_throttled_per_account_and_address(client, seeded, use_settings, db):
    use_settings(login_rate_per_window=3)
    assert [login(client, pw="wrong").status_code for _ in range(3)] == [401, 401, 401]
    blocked = login(client)  # even the right password is refused during the window
    assert blocked.status_code == 429 and "retry-after" in blocked.headers
    assert login(client, email="reception@demo.hotel").status_code == 200  # other account fine
    failed = db.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login_failed")).all()
    assert len(failed) == 3 and all("password" in e.details["reason"] for e in failed)


def test_unknown_accounts_are_handled_uniformly_and_not_audited(client, seeded, db):
    a = login(client, email="nobody@demo.hotel", pw="whatever")
    b = login(client, pw="wrong")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    assert db.query(AuditEvent).filter(AuditEvent.action == "auth.login_failed").count() == 1


def test_forwarded_for_is_only_trusted_behind_a_proxy(client, seeded, use_settings):
    use_settings(login_rate_per_window=2, trust_proxy_headers=False)
    for i in range(2):
        login(client, pw="wrong", headers={"X-Forwarded-For": f"10.0.0.{i}"})
    # spoofing a new address does not reset the counter when the header is not trusted
    assert login(client, headers={"X-Forwarded-For": "10.9.9.9"}).status_code == 429
    use_settings(login_rate_per_window=2, trust_proxy_headers=True)
    assert login(client, headers={"X-Forwarded-For": "10.7.7.7"}).status_code == 200
    assert login(client, headers={"X-Forwarded-For": "not-an-ip"}).status_code in (200, 429)


# ------------------------------------------------------------------ secrets provider

class StubSecrets:
    def __init__(self, payload):
        self.payload = payload

    def get_secret_value(self, SecretId):  # noqa: N803
        if isinstance(self.payload, Exception):
            raise self.payload
        return {"SecretString": self.payload}


def test_secrets_provider_overlays_only_allowed_keys():
    s = Settings(secrets_provider="aws", secrets_id="prod/hospitality")
    payload = json.dumps({"APP_SECRET_KEY": "k" * 40, "twilio_auth_token": "tok",
                          "environment": "development", "log_level": "DEBUG", "x": 1})
    out = load_secret_overrides(s, StubSecrets(payload))
    assert out == {"app_secret_key": "k" * 40, "twilio_auth_token": "tok"}
    assert s.model_copy(update=out).app_secret_key == "k" * 40


def test_secrets_provider_errors_never_leak_secret_material():
    s = Settings(secrets_provider="aws", secrets_id="x")
    with pytest.raises(RuntimeError) as e:
        load_secret_overrides(s, StubSecrets("not json {top-secret-value"))
    assert "top-secret" not in str(e.value)
    with pytest.raises(RuntimeError):
        load_secret_overrides(s, StubSecrets(json.dumps(["list"])))
    with pytest.raises(RuntimeError) as e2:
        load_secret_overrides(s, StubSecrets(PermissionError("denied: arn:aws:secret")))
    assert "arn:aws" not in str(e2.value)
    with pytest.raises(ConfigError):
        load_secret_overrides(Settings(secrets_provider="aws", secrets_id=""), StubSecrets("{}"))
    with pytest.raises(ConfigError):
        load_secret_overrides(Settings(secrets_provider="vault"), StubSecrets("{}"))


def test_production_requires_a_metrics_token_and_valid_limiter_backend():
    base = dict(environment="production", app_secret_key="x" * 48, vector_store_provider="pinecone",
                pinecone_api_key="k", embedding_provider="sentence-transformers",
                metrics_token="m")
    Settings(**base).validate_for_runtime()
    with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
        Settings(**{**base, "metrics_token": ""}).validate_for_runtime()
    with pytest.raises(RuntimeError, match="RATE_LIMIT_BACKEND"):
        Settings(**base, rate_limit_backend="memcached").validate_for_runtime()


def test_db_roles_still_load_for_sanity(db, seeded):
    assert db.scalar(select(User).where(User.email == "admin@demo.hotel")).token_version == 0
