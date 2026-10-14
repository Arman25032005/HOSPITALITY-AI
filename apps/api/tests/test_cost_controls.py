from sqlalchemy import select

from app.agents import tools
from app.agents.llm_tasks import IntentDecision
from app.llm.scripted import ScriptedLLM
from app.models import AgentExecution
from app.rag.cache import AnswerCache
from tests.conftest import HOTEL_ID

BASE = f"/api/v1/hotels/{HOTEL_ID}"
POLICY = "# Stay Policies\n\n## Check-in and Check-out\n\nCheck-out time is 2:00 PM.\n"


# ------------------------------------------------------------------ daily token budget

def test_daily_token_budget_stops_llm_calls_but_not_the_product(chat, use_llm, use_settings, db):
    llm = use_llm(ScriptedLLM(lambda s, t: IntentDecision(intent="smalltalk", confidence=0.9)))
    use_settings(llm_daily_token_budget_per_hotel=20)       # each scripted call costs 15 tokens
    first = chat().say("asdf one")
    second = chat().say("asdf two")
    assert "welcome" in first["reply"].lower() and "welcome" in second["reply"].lower()
    assert len(llm.calls) == 2                               # 15 used < 20, so a 2nd call was allowed
    third = chat().say("asdf three")                         # 30 used >= 20: budget exhausted
    assert len(llm.calls) == 2                               # no further LLM spend
    assert third["outcome"] == "abstained"                   # deterministic fallback still answers
    ex = db.scalars(select(AgentExecution).order_by(AgentExecution.created_at)).all()[-1]
    assert any("budget exhausted" in t["info"] for t in ex.trace)


def test_zero_budget_means_unlimited(chat, use_llm, use_settings):
    llm = use_llm(ScriptedLLM(lambda s, t: IntentDecision(intent="smalltalk", confidence=0.9)))
    use_settings(llm_daily_token_budget_per_hotel=0)
    for i in range(4):
        chat().say(f"asdf {i}")
    assert len(llm.calls) == 4


def test_llm_latency_is_recorded_per_execution(chat, use_llm, db):
    use_llm(ScriptedLLM(lambda s, t: IntentDecision(intent="smalltalk", confidence=0.9)))
    chat().say("asdf")
    ex = db.scalars(select(AgentExecution)).all()[-1]
    assert ex.tokens_in == 10 and ex.tokens_out == 5 and ex.llm_ms >= 0


# ------------------------------------------------------------------ answer cache

def spy(monkeypatch):
    calls = {"n": 0}
    real = tools.answer_question

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(tools, "answer_question", counting)
    return calls


def test_repeat_questions_are_served_from_cache(chat, monkeypatch):
    calls = spy(monkeypatch)
    a = chat().say("What time is check-out?")
    b = chat().say("  what TIME is   check-out? ")
    assert calls["n"] == 1 and a["reply"] == b["reply"] and "11:00 AM" in b["reply"]
    chat().say("What time is breakfast on weekends?")
    assert calls["n"] == 2


def test_cache_is_invalidated_when_the_knowledge_base_changes(chat, client, auth, monkeypatch):
    calls = spy(monkeypatch)
    assert "11:00 AM" in chat().say("What time is check-out?")["reply"]
    r = client.post(f"{BASE}/knowledge/documents", headers=auth("manager@demo.hotel"),
                    data={"title": "Stay Policies", "doc_type": "policy", "doc_key": "stay-policies"},
                    files={"file": ("p.md", POLICY.encode())})
    assert r.status_code == 201
    fresh = chat().say("What time is check-out?")
    assert "2:00 PM" in fresh["reply"] and "11:00 AM" not in fresh["reply"]
    assert calls["n"] == 2                       # recomputed after the document changed
    off = client.post(f"{BASE}/knowledge/documents/{r.json()['id']}/deactivate",
                      headers=auth("manager@demo.hotel"))
    assert off.status_code == 200
    assert "2:00 PM" not in chat().say("What time is check-out?")["reply"]  # deactivation too


def test_cache_can_be_disabled_and_is_per_hotel(chat, use_settings, monkeypatch, other_org, client, db):
    calls = spy(monkeypatch)
    use_settings(answer_cache_ttl_seconds=0)
    chat().say("What time is check-out?")
    chat().say("What time is check-out?")
    assert calls["n"] == 2
    use_settings(answer_cache_ttl_seconds=300)
    calls["n"] = 0
    from app.models import Hotel, Organization
    h = db.get(Hotel, other_org["hotel_id"])
    slug = db.get(Organization, h.organization_id).slug
    chat().say("What time is check-out?")
    tok = client.post("/api/v1/chat/sessions", json={"org_slug": slug, "hotel_slug": h.slug}).json()["token"]
    other = client.post("/api/v1/chat/messages", headers={"Authorization": f"Bearer {tok}"},
                        json={"text": "What time is check-out?", "client_message_id": "c1"}).json()
    assert "11:00 AM" not in other["reply"] and calls["n"] == 2   # no cross-hotel cache leak


def test_answer_cache_ttl_and_capacity():
    t = [0.0]
    c = AnswerCache(clock=lambda: t[0], max_items=2)
    c.put(("a",), 1, ttl=10)
    assert c.get(("a",)) == 1
    t[0] = 11
    assert c.get(("a",)) is None
    c.put(("a",), 1, 10)
    c.put(("b",), 2, 10)
    c.put(("c",), 3, 10)                     # evicts the oldest entry
    assert c.get(("a",)) is None and c.get(("c",)) == 3
