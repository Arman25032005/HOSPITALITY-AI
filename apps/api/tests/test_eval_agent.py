"""The evaluation harness itself must be trustworthy: scoring is pure and tested here, and two
scenarios are run end to end (one passing, one forced to fail) to prove it can detect failure."""
import json

import pytest
from sqlalchemy import select

from app.models import AgentExecution
from scripts import eval_agent as ea


def test_check_expectations_detects_each_kind_of_failure():
    obs = {"reply": "Check-out is 11:00 AM.", "outcome": "answered", "intent": "knowledge",
           "tools": ["search_knowledge"], "awaiting_confirmation": False}
    assert ea.check_expectations({"intent": "knowledge", "outcome": "answered",
                                  "tools": ["search_knowledge"], "contains": ["11:00 am"],
                                  "not_contains": ["refund"], "not_outcome": "error",
                                  "awaiting_confirmation": False}, obs) == []
    bad = ea.check_expectations({"intent": "reservation", "outcome": "escalated",
                                 "tools": [], "contains": ["pool"], "not_contains": ["check-out"],
                                 "not_outcome": "answered", "awaiting_confirmation": True}, obs)
    assert len(bad) == 7


def test_check_final_and_templates():
    assert ea.check_final({"tickets": 1}, {"tickets": 1}) == []
    assert ea.check_final({"tickets": 1}, {"tickets": 0}) == ["final tickets: 0 != 1"]
    assert ea.render_template("{ci} to {co}", {"ci": "A", "co": "B"}) == "A to B"


def _sc(id_, metric, passed, esc=None, pred=False, steps=(), calls=()):
    return {"id": id_, "metric": metric, "passed": passed, "escalation": esc,
            "predicted_escalation": pred, "steps": list(steps), "tool_calls": list(calls)}


def test_metric_arithmetic():
    rows = [
        _sc("a", "policy", True), _sc("b", "policy", False),
        _sc("c", "routing", True, esc=True, pred=True,
            steps=[{"intent": "x", "expected_intent": "x", "tools": ["t"], "expected_tools": ["t"]}],
            calls=[{"name": "t", "error": ""}]),
        _sc("d", "routing", True, esc=False, pred=True,
            steps=[{"intent": "y", "expected_intent": "x", "tools": [], "expected_tools": ["t"]}],
            calls=[{"name": "t", "error": "invalid tool arguments"}]),
        _sc("e", "routing", True, esc=True, pred=False),
    ]
    m = ea.compute_metrics(rows)
    assert m["policy_compliance_rate"] == 0.5
    assert m["intent_routing_accuracy"] == 0.5 and m["tool_selection_accuracy"] == 0.5
    assert m["tool_argument_validity"] == 0.5
    assert m["escalation_precision"] == 0.5 and m["escalation_recall"] == 0.5
    assert m["workflow_completion_rate"] is None  # no scenarios of that kind: not 0, not 1
    assert ea.critical_failures(rows) == ["b", "e"]  # failed policy + missed escalation


def test_database_guard_refuses_non_eval_databases(monkeypatch):
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/hospitality")
    with pytest.raises(SystemExit):
        ea._guard_database(False)
    ea._guard_database(True)
    monkeypatch.undo()  # restore DATABASE_URL, then drop the cached settings built from the fake
    get_settings.cache_clear()
    assert isinstance(get_settings(), Settings)
    assert ea._guard_database(False).endswith("_test")


def test_harness_runs_scenarios_and_detects_a_failing_expectation(tmp_path):
    ds = {"version": "t", "scenarios": [
        {"id": "ok", "metric": "routing", "steps": [{"say": "What time is check-out?", "expect": {
            "intent": "knowledge", "tools": ["search_knowledge"], "contains": ["11:00 AM"]}}]},
        {"id": "bad", "metric": "policy", "steps": [{"say": "What time is check-out?", "expect": {
            "contains": ["definitely not in the reply"]}}]},
    ]}
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(ds))
    # the dataset path must live under ROOT for relative_to; copy into the evals dir
    inside = ea.ROOT / "evals" / "agent" / "_tmp_test_ds.json"
    inside.write_text(json.dumps(ds))
    try:
        report = ea.run(inside, tmp_path / "out.json", None, allow=False)
    finally:
        inside.unlink()
    assert [f["id"] for f in report["failures"]] == ["bad"]
    assert report["critical_failures"] == ["bad"]
    assert report["metrics"]["intent_routing_accuracy"] == 1.0
    assert json.loads((tmp_path / "out.json").read_text())["run"]["git_sha"]


def test_emergency_turns_record_their_intent(chat, db):
    r = chat().say("There is smoke coming from the vent in my room")
    assert r["outcome"] == "escalated"
    assert db.scalars(select(AgentExecution.intent)).all()[-1] == "emergency"


def test_new_request_supersedes_an_unanswered_handoff_offer_but_not_a_booking(chat, db):
    from datetime import date, timedelta

    c = chat()
    c.say("asdf qwer")
    r = c.say("zxcv tyui")  # second abstain offers a handoff
    assert r["awaiting_confirmation"]
    again = c.say("What time is check-out?")
    assert "11:00 AM" in again["reply"] and "waiting for your answer" not in again["reply"].lower()

    # booking confirmations remain strict: an unrelated message does not silently drop them
    ci = date.today() + timedelta(days=20)
    c2 = chat()
    c2.say("I'd like to book a room")
    c2.say(f"{ci} to {ci + timedelta(days=2)} for 2 adults")
    c2.say("Deluxe King")
    assert c2.say("Eval Booker")["awaiting_confirmation"]
    r = c2.say("my TV is not working")
    assert "waiting for your answer" in r["reply"].lower() and r["awaiting_confirmation"]
