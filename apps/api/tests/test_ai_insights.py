from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.agents.llm_tasks import IntentDecision
from app.llm.scripted import ScriptedLLM
from app.models import AuditEvent, ServiceRequest
from tests.conftest import DEMO_PASSWORD, HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}"


def future(offset=30):
    ci = date.today() + timedelta(days=offset)
    return ci, ci + timedelta(days=2)


@pytest.fixture()
def traffic(chat, client, in_house, use_llm, auth):
    """Six conversations with known outcomes."""
    use_llm(ScriptedLLM(lambda s, t: IntentDecision(intent="smalltalk", confidence=0.9)))
    out = {}
    a = chat()
    out["a1"] = a.say("What time is check-out?")
    out["a2"] = a.say("Do you have a casino? call me on +1 415 555 0100")
    b = chat()
    b.say("I want to speak to a manager")
    c = chat()
    ci, co = future()
    for text in ("I'd like to book a room", f"{ci} to {co} for 2 adults", "Deluxe King", "Jane Doe", "yes"):
        c.say(text)
    d = chat()
    d.say("I'd like to book a room")
    e = chat()
    e.verify(in_house[0], "Doe")
    e.say("my TV is not working")
    f = chat()
    f.say("asdf")                                   # LLM classifies -> 10 in / 5 out tokens
    out.update(a=a, b=b, c=c, d=d, e=e, f=f)
    return out


def get(client, auth, path, who="manager@demo.hotel", **params):
    return client.get(f"{BASE}/{path}", params=params, headers=auth(who))


def test_kpis_match_the_underlying_records(traffic, client, auth, db):
    k = get(client, auth, "ai/kpis").json()
    assert k["conversations"] == k["responded_conversations"] == 6
    # resolved: reservation booked (c), ticket logged (e), smalltalk answered (f)
    assert (k["resolved_conversations"], k["handed_off_conversations"],
            k["unresolved_conversations"]) == (3, 1, 2)
    assert k["resolution_rate"] == 0.5 and k["handoff_rate"] == 0.167
    assert k["knowledge_abstain_rate"] == 0.5                   # casino question abstained
    assert (k["reservation_flows_started"], k["reservation_completion_rate"]) == (2, 0.5)
    assert k["agent_tickets_created"] == 2                      # handoff ticket + TV ticket
    assert k["agent_ticket_resolution_rate"] == 0.0
    # 3 LLM calls: slot extraction on both "book a room" openers + the ambiguous "asdf" intent
    assert k["tokens_in"] == 30 and k["tokens_out"] == 15
    assert k["estimated_cost_usd"] == pytest.approx(0.00042)    # 3 x (10*4 + 5*20) / 1e6
    assert k["estimated_cost_per_conversation_usd"] == pytest.approx(0.00042 / 6, abs=1e-6)
    assert k["model_latency_avg_ms"] is not None
    assert k["turns"] == sum(k["outcomes"].values())
    assert k["turn_latency_p50_ms"] is not None and k["turn_latency_p95_ms"] >= k["turn_latency_p50_ms"]
    # resolving the agent-created ticket moves the ticket KPI
    tv = db.scalar(select(ServiceRequest).where(ServiceRequest.category == "maintenance"))
    rec = auth("reception@demo.hotel")
    for st in ("in_progress", "resolved"):
        client.post(f"{BASE}/tickets/{tv.id}/status", json={"status": st}, headers=rec)
    assert get(client, auth, "ai/kpis").json()["agent_ticket_resolution_rate"] == 0.5


def test_responded_is_distinct_from_resolved(traffic, client, auth):
    k = get(client, auth, "ai/kpis").json()
    assert k["responded_conversations"] > k["resolved_conversations"]


def test_staff_chat_and_old_data_do_not_pollute_kpis(traffic, client, auth):
    client.post(f"{BASE}/ops/chat", headers=auth("manager@demo.hotel"),
                json={"text": "summary for the last 7 days", "client_message_id": "k1"})
    assert get(client, auth, "ai/kpis").json()["conversations"] == 6


def test_execution_traces_are_sanitised_and_complete(traffic, client, auth):
    rows = get(client, auth, "ai/executions", limit=100).json()
    assert len(rows) == 11          # 2 + 1 + 5 + 1 + 1 + 1 guest turns; staff chat excluded
    f = next(r for r in rows if r["intent"] == "smalltalk")
    assert f["estimated_cost_usd"] == pytest.approx(0.00014) and f["intent"] == "smalltalk"
    assert f["trace"][0]["node"] == "gate" and f["steps"] >= 3 and f["correlation_id"]
    k = next(r for r in rows if r["intent"] == "knowledge" and r["tools"])
    assert k["tools"][0]["tool"] == "search_knowledge" and k["tools"][0]["success"] is True
    dumped = str(rows)
    assert "415 555 0100" not in dumped and "+1 415" not in dumped      # PII redacted in tool args
    only = get(client, auth, "ai/executions", outcome="handoff").json()
    assert only and all(r["outcome"] == "handoff" for r in only)
    assert len(get(client, auth, "ai/executions", limit=3).json()) == 3


def test_tool_stats_unanswered_and_flagged(traffic, client, auth):
    stats = {t["tool"]: t for t in get(client, auth, "ai/tools").json()}
    assert stats["search_knowledge"]["calls"] >= 2 and stats["search_knowledge"]["success_rate"] == 1.0
    assert stats["create_service_ticket"]["calls"] == 1
    un = get(client, auth, "ai/unanswered").json()
    q = next(u for u in un if u["outcome"] == "abstained")
    assert "casino" in q["question"] and "[phone]" in q["question"] and "415" not in q["question"]
    # guests mark the unhelpful answer; managers see it flagged with question + answer
    a = traffic["a"]
    client.post(f"/api/v1/chat/messages/{traffic['a1']['message_id']}/feedback", headers=a.headers,
                json={"rating": 5})
    client.post(f"/api/v1/chat/messages/{traffic['a2']['message_id']}/feedback", headers=a.headers,
                json={"rating": 1, "comment": "call me back on 415-555-0199 please"})
    flagged = get(client, auth, "ai/flagged").json()
    assert len(flagged) == 1 and flagged[0]["rating"] == 1 and "casino" in flagged[0]["question"]
    assert "0199" not in str(flagged) and "[phone]" in flagged[0]["comment"]
    k = get(client, auth, "ai/kpis").json()
    assert (k["avg_feedback_rating"], k["feedback_count"], k["low_rated_answers"]) == (3.0, 2, 1)


def test_access_control_and_validation(traffic, client, auth, db):
    for path in ("ai/kpis", "ai/executions", "ai/tools", "ai/unanswered", "ai/flagged", "audit"):
        assert get(client, auth, path, who="reception@demo.hotel").status_code == 403, path
        assert get(client, auth, path, who="housekeeping@demo.hotel").status_code == 403, path
        assert client.get(f"{BASE}/{path}", headers=traffic["a"].headers).status_code == 401, path
        assert client.get(f"{BASE}/{path}").status_code == 401, path
    assert get(client, auth, "ai/kpis", days=0).status_code == 422
    assert get(client, auth, "ai/kpis", days=500).status_code == 422


def test_ai_insights_are_tenant_scoped(traffic, client, other_org):
    tok = client.post("/api/v1/auth/login", json={"email": other_org["email"],
                                                  "password": DEMO_PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"{BASE}/ai/kpis", headers=h).status_code == 404
    mine = client.get(f"/api/v1/hotels/{other_org['hotel_id']}/ai/kpis", headers=h).json()
    assert mine["conversations"] == 0 and mine["resolution_rate"] is None


def test_audit_log_filters_and_names_actors(traffic, client, auth, db):
    rows = get(client, auth, "audit", limit=500).json()
    assert {"ticket.created", "reservation.created"} <= {r["action"] for r in rows}
    only = get(client, auth, "audit", action="reservation").json()
    assert only and all(r["action"].startswith("reservation") for r in only)
    assert get(client, auth, "audit", entity_type="service_request").json()
    login = get(client, auth, "audit", action="auth.login").json()
    assert login == [] or all(r["actor_type"] == "user" for r in login)
    created = next(r for r in rows if r["action"] == "ticket.created")
    assert created["actor_type"] == "agent" and created["actor_name"] is None
    assert db.query(AuditEvent).count() >= len(rows) - 5
