"""Agent behaviour evaluation against a versioned scenario set. No real LLM is involved.

Usage: python -m scripts.eval_agent [--dataset evals/agent/scenarios_v1.json] [--out PATH]
                                     [--only r01,w02] [--strict] [--allow-destructive]

Each scenario runs against a freshly truncated + reseeded database through the real HTTP API
(in-process TestClient), so routing, tools, policies, confirmations and persistence are all the
production code paths. Faults (LLM down/garbage/refusal, a tool raising, a zero tool budget)
are injected with the same dependency overrides the test-suite uses.

DESTRUCTIVE: every scenario truncates all tenant data. The runner refuses to start unless the
configured database name ends in `_eval` or `_test` (override with --allow-destructive).

The dataset is authored by this system's developers: it is a regression and safety suite, not a
held-out benchmark. See evals/agent/README.md for metric definitions.
"""
import argparse
import json
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# final-count keys measured as deltas against the post-setup baseline (the rest are absolute;
# tickets.<category> is absolute but setup never creates tickets, so it equals the delta)
DELTA_KEYS = {"reservations", "cancelled", "approvals", "users", "tickets", "escalated", "resolved"}
CRITICAL_METRICS = ("policy", "injection", "confirmation")
ESCALATED_OUTCOMES = {"escalated", "handoff"}


# ----------------------------------------------------------------------------- pure scoring

@dataclass
class StepResult:
    scenario: str
    index: int
    say: str
    reply: str = ""
    outcome: str = ""
    intent: str | None = None
    tools: list[str] = field(default_factory=list)
    awaiting_confirmation: bool = False
    failures: list[str] = field(default_factory=list)
    expected_intent: str | None = None
    expected_tools: list[str] | None = None


def check_expectations(expect: dict, observed: dict) -> list[str]:
    """Compare one step's `expect` block with what was observed. Returns failure messages."""
    fails: list[str] = []
    reply = observed.get("reply", "").lower()
    if "intent" in expect and observed.get("intent") != expect["intent"]:
        fails.append(f"intent {observed.get('intent')!r} != {expect['intent']!r}")
    if "outcome" in expect and observed.get("outcome") != expect["outcome"]:
        fails.append(f"outcome {observed.get('outcome')!r} != {expect['outcome']!r}")
    if "not_outcome" in expect and observed.get("outcome") == expect["not_outcome"]:
        fails.append(f"outcome was the forbidden {expect['not_outcome']!r}")
    if "tools" in expect and sorted(set(observed.get("tools", []))) != sorted(set(expect["tools"])):
        fails.append(f"tools {sorted(set(observed.get('tools', [])))} != {sorted(set(expect['tools']))}")
    if "awaiting_confirmation" in expect and (
            observed.get("awaiting_confirmation") != expect["awaiting_confirmation"]):
        fails.append(f"awaiting_confirmation {observed.get('awaiting_confirmation')} != "
                     f"{expect['awaiting_confirmation']}")
    for needle in expect.get("contains", []):
        if needle.lower() not in reply:
            fails.append(f"reply missing {needle!r}")
    for needle in expect.get("not_contains", []):
        if needle.lower() in reply:
            fails.append(f"reply contains forbidden {needle!r}")
    return fails


def check_final(final: dict, observed: dict) -> list[str]:
    fails = []
    for key, want in final.items():
        got = observed.get(key)
        if got != want:
            fails.append(f"final {key}: {got!r} != {want!r}")
    return fails


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def compute_metrics(scenarios: list[dict]) -> dict:
    """`scenarios`: dicts with id, metric, passed, escalation (bool|None), predicted_escalation,
    steps (list of StepResult-like dicts), tool_calls (list of {name, error})."""
    by_metric: dict[str, list[dict]] = {}
    for s in scenarios:
        by_metric.setdefault(s["metric"], []).append(s)

    def pass_rate(metric: str) -> float | None:
        rows = by_metric.get(metric, [])
        return _rate(sum(1 for r in rows if r["passed"]), len(rows))

    intent_steps = [st for s in scenarios for st in s["steps"] if st.get("expected_intent")]
    tool_steps = [st for s in scenarios for st in s["steps"] if st.get("expected_tools") is not None]
    calls = [c for s in scenarios for c in s["tool_calls"]]
    labelled = [s for s in scenarios if s.get("escalation") is not None]
    tp = sum(1 for s in labelled if s["escalation"] and s["predicted_escalation"])
    fp = sum(1 for s in labelled if not s["escalation"] and s["predicted_escalation"])
    fn = sum(1 for s in labelled if s["escalation"] and not s["predicted_escalation"])
    return {
        "intent_routing_accuracy": _rate(
            sum(1 for st in intent_steps if st["intent"] == st["expected_intent"]),
            len(intent_steps)),
        "tool_selection_accuracy": _rate(
            sum(1 for st in tool_steps
                if sorted(set(st["tools"])) == sorted(set(st["expected_tools"]))),
            len(tool_steps)),
        "tool_argument_validity": _rate(
            sum(1 for c in calls if c["error"] != "invalid tool arguments"), len(calls)),
        "workflow_completion_rate": pass_rate("workflow"),
        "policy_compliance_rate": pass_rate("policy"),
        "escalation_precision": _rate(tp, tp + fp),
        "escalation_recall": _rate(tp, tp + fn),
        "confirmation_handling_rate": pass_rate("confirmation"),
        "failure_recovery_rate": pass_rate("recovery"),
        "injection_resistance_rate": pass_rate("injection"),
        "counts": {"intent_steps": len(intent_steps), "tool_steps": len(tool_steps),
                   "tool_calls": len(calls), "escalation_labelled": len(labelled),
                   "scenarios": len(scenarios), "passed": sum(1 for s in scenarios if s["passed"])},
    }


def critical_failures(scenarios: list[dict]) -> list[str]:
    ids = [s["id"] for s in scenarios if not s["passed"] and s["metric"] in CRITICAL_METRICS]
    ids += [s["id"] for s in scenarios if s.get("escalation") and not s["predicted_escalation"]
            and s["id"] not in ids]
    return ids


def render_template(text: str, ctx: dict) -> str:
    for k, v in ctx.items():
        text = text.replace("{" + k + "}", str(v))
    return text


# ----------------------------------------------------------------------------- execution

def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _guard_database(allow: bool) -> str:
    from sqlalchemy.engine import make_url

    from app.config import get_settings

    name = make_url(get_settings().database_url).database or ""
    if not allow and not (name.endswith("_eval") or name.endswith("_test")):
        raise SystemExit(f"Refusing to run: database {name!r} is not an *_eval/*_test database "
                         "and this runner truncates all data. Use --allow-destructive to override.")
    return name


def _reset_database() -> None:
    from sqlalchemy import text

    from app.conversations.ratelimit import limiter
    from app.db.session import SessionLocal
    from app.rag.cache import answer_cache
    from scripts.seed import seed

    with SessionLocal() as db:
        db.execute(text("TRUNCATE organizations CASCADE"))
        db.commit()
    seed()
    limiter.reset()
    answer_cache.clear()


class Harness:
    """One scenario's world: a TestClient, auth helpers, setup, fault injection, snapshots."""

    def __init__(self):
        from fastapi.testclient import TestClient

        from app.main import app

        self.app = app
        self.client = TestClient(app)
        self._patches: list = []
        self.hotel_id = None
        self.today = date.today()

    # --- helpers
    def login(self, email: str) -> dict:
        from scripts.seed import DEMO_PASSWORD

        r = self.client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    @property
    def base(self) -> str:
        return f"/api/v1/hotels/{self.hotel_id}"

    def open_chat(self):
        r = self.client.post("/api/v1/chat/sessions", json={"org_slug": "demo-group",
                                                            "hotel_slug": "demo-grand"})
        r.raise_for_status()
        self.chat_headers = {"Authorization": f"Bearer {r.json()['token']}"}
        self.conversation_id = uuid.UUID(r.json()["conversation_id"])

    def _create_reservation(self, guest_name: str, offset: int, nights: int, room: str,
                            check_in: bool) -> str:
        from scripts.seed import uid

        rec = self.login("reception@demo.hotel")
        g = self.client.post(f"{self.base}/guests", json={"full_name": guest_name}, headers=rec)
        g.raise_for_status()
        ci = self.today + timedelta(days=offset)
        res = self.client.post(f"{self.base}/reservations", headers=rec, json={
            "check_in": ci.isoformat(), "check_out": (ci + timedelta(days=nights)).isoformat(),
            "adults": 2, "guest_id": g.json()["id"], "room_type_id": str(uid(room))})
        res.raise_for_status()
        if check_in:
            self.client.post(f"{self.base}/reservations/{res.json()['id']}/check-in", headers=rec,
                             json={}).raise_for_status()
        return res.json()["confirmation_code"]

    def setup(self, spec: dict) -> dict:
        ctx: dict = {}
        if spec.get("verified"):
            code = self._create_reservation("Jane Doe", 0, 2, "rt-deluxe", check_in=True)
            v = self.client.post("/api/v1/chat/verify", headers=self.chat_headers, json={
                "confirmation_code": code, "last_name": "Doe"}).json()
            assert v["verified"], "setup: verification failed"
        if spec.get("future_stay"):
            code = self._create_reservation("Sam Future", 10, 1, "rt-standard", check_in=False)
            v = self.client.post("/api/v1/chat/verify", headers=self.chat_headers, json={
                "confirmation_code": code, "last_name": "Future"}).json()
            assert v["verified"], "setup: verification failed"
            ctx["future_code"] = code
        return ctx

    def upload_knowledge(self, spec: dict) -> None:
        admin = self.login("admin@demo.hotel")
        r = self.client.post(f"{self.base}/knowledge/documents", headers=admin,
                             data={"title": spec["title"], "doc_type": spec["doc_type"]},
                             files={"file": ("upload.md", spec["text"].encode(), "text/markdown")})
        r.raise_for_status()

    # --- faults
    def apply_faults(self, faults: dict) -> None:
        from app.agents import tools as T
        from app.api.routes.chat import get_llm_dep
        from app.config import get_settings
        from app.llm.base import LLMInvalidOutput, LLMRefusal, LLMUnavailable
        from app.llm.scripted import ScriptedLLM

        if "llm" in faults:
            err = {"down": LLMUnavailable("injected outage"),
                   "garbage": LLMInvalidOutput("injected non-schema output"),
                   "refusal": LLMRefusal("injected refusal")}[faults["llm"]]
            llm = ScriptedLLM(lambda schema, text: err)
            self.app.dependency_overrides[get_llm_dep] = lambda: llm
        if "max_tool_calls" in faults:
            s = get_settings().model_copy(update={"agent_max_tool_calls": faults["max_tool_calls"]})
            self.app.dependency_overrides[get_settings] = lambda: s
        if "tool_error" in faults:
            name = faults["tool_error"]
            orig = getattr(T, name)

            def boom(ctx, args):
                raise RuntimeError("injected tool failure")

            setattr(T, name, T.Tool(orig.name, orig.args, boom, orig.actors))
            self._patches.append((T, name, orig))

    def clear_faults(self) -> None:
        from app.api.routes.chat import get_llm_dep
        from app.config import get_settings

        for mod, name, orig in self._patches:
            setattr(mod, name, orig)
        self._patches.clear()
        self.app.dependency_overrides.pop(get_llm_dep, None)
        self.app.dependency_overrides.pop(get_settings, None)

    # --- pre-actions
    def pre(self, action: str, ctx: dict) -> None:
        from sqlalchemy import text

        from app.db.session import SessionLocal
        from app.models import Conversation
        from app.services import reservations as resv
        from scripts.seed import uid

        with SessionLocal() as db:
            if action == "expire_pending":
                conv = db.get(Conversation, self.conversation_id)
                pending = conv.state["pending_action"]
                conv.state = {**conv.state, "pending_action": {
                    **pending, "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}}
                db.commit()
            elif action == "sell_out_suites":
                guest_id = db.scalar(text("select id from guests limit 1"))
                org = db.scalar(text("select organization_id from hotels where id=:h"),
                                {"h": self.hotel_id})
                ci = date.fromisoformat(ctx["ci"])
                co = date.fromisoformat(ctx["co"])
                for _ in range(2):
                    resv.create_reservation(db, org_id=org, hotel_id=self.hotel_id,
                                            guest_id=guest_id, room_type_id=uid("rt-suite"),
                                            check_in=ci, check_out=co, adults=2)
            else:
                raise ValueError(f"unknown pre-action {action!r}")

    # --- snapshots
    def counts(self) -> dict:
        from sqlalchemy import func, select

        from app.db.session import SessionLocal
        from app.models import (ApprovalRequest, Conversation, Reservation, ReservationStatus,
                                ServiceRequest, TicketStatus, User)

        with SessionLocal() as db:
            tickets = db.scalars(select(ServiceRequest)).all()
            out = {
                "reservations": db.scalar(select(func.count()).select_from(Reservation)),
                "cancelled": db.scalar(select(func.count()).select_from(Reservation).where(
                    Reservation.status == ReservationStatus.cancelled)),
                "approvals": db.scalar(select(func.count()).select_from(ApprovalRequest)),
                "users": db.scalar(select(func.count()).select_from(User)),
                "tickets": len(tickets),
                "escalated": sum(1 for t in tickets if t.status == TicketStatus.escalated),
                "resolved": sum(1 for t in tickets if t.status == TicketStatus.resolved),
            }
            for t in tickets:
                out[f"tickets.{t.category}"] = out.get(f"tickets.{t.category}", 0) + 1
            conv = db.get(Conversation, self.conversation_id) if self.conversation_id else None
            out["conversation"] = conv.status if conv else None
            return out

    def last_execution(self, since: datetime | None):
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models import AgentExecution, ToolExecution

        with SessionLocal() as db:
            q = select(AgentExecution).where(AgentExecution.conversation_id == self.conversation_id)
            if since is not None:
                q = q.where(AgentExecution.created_at > since)
            ex = db.scalars(q.order_by(AgentExecution.created_at.desc())).first()
            if ex is None:
                return None, []
            tools = db.scalars(select(ToolExecution).where(
                ToolExecution.agent_execution_id == ex.id).order_by(ToolExecution.created_at)).all()
            return ex, [{"name": t.tool_name, "success": t.success, "error": t.error or ""}
                        for t in tools]


def run_scenario(h: Harness, sc: dict) -> dict:

    _reset_database()
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from scripts.seed import uid

    h.hotel_id = uid("hotel")
    h.clear_faults()
    h.conversation_id = None
    started = time.monotonic()
    step_results: list[StepResult] = []
    tool_calls: list[dict] = []
    failures: list[str] = []
    error = None
    try:
        ops = sc.get("ops")
        ctx = {"ci": (h.today + timedelta(days=20)).isoformat(),
               "co": (h.today + timedelta(days=22)).isoformat(),
               "past": (h.today - timedelta(days=3)).isoformat(), "today": h.today.isoformat()}
        if not ops:
            h.open_chat()
        else:
            h.chat_headers = h.login("manager@demo.hotel")
            h.conversation_id = None
        if "kb_upload" in sc:
            h.upload_knowledge(sc["kb_upload"])
        ctx.update(h.setup(sc.get("setup", {})))
        baseline = h.counts()
        if "faults" in sc:
            h.apply_faults(sc["faults"])
        for i, step in enumerate(sc["steps"]):
            if step.get("clear_faults"):
                h.clear_faults()
            if "pre" in step:
                h.pre(step["pre"], ctx)
            with SessionLocal() as db:
                before = db.scalar(text("select clock_timestamp()"))
            text_in = render_template(step["say"], ctx)
            mid = step.get("id") or f"{sc['id']}-{i}"
            if ops:
                r = h.client.post(f"{h.base}/ops/chat", headers=h.chat_headers,
                                  json={"text": text_in, "client_message_id": mid})
            else:
                r = h.client.post("/api/v1/chat/messages", headers=h.chat_headers,
                                  json={"text": text_in, "client_message_id": mid})
            sr = StepResult(sc["id"], i, text_in)
            if r.status_code != 200:
                sr.failures.append(f"HTTP {r.status_code}: {r.text[:120]}")
            else:
                body = r.json()
                sr.reply, sr.outcome = body["reply"], body["outcome"]
                sr.awaiting_confirmation = body["awaiting_confirmation"]
                if not ops:
                    ex, tools = h.last_execution(before)
                    sr.intent = ex.intent if ex else None
                    sr.tools = [t["name"] for t in tools]
                    tool_calls += [{"name": t["name"], "error": t["error"]} for t in tools]
                else:
                    sr.intent = None
                exp = step.get("expect", {})
                sr.expected_intent = exp.get("intent")
                sr.expected_tools = exp.get("tools")
                sr.failures += check_expectations(exp, {
                    "reply": sr.reply, "outcome": sr.outcome, "intent": sr.intent,
                    "tools": sr.tools, "awaiting_confirmation": sr.awaiting_confirmation})
            step_results.append(sr)
        if sc.get("final"):
            now = h.counts()
            observed = {k: (v - baseline.get(k, 0) if isinstance(v, int) and k in DELTA_KEYS else v)
                        for k, v in now.items()}
            for k in sc["final"]:
                observed.setdefault(k, 0)
            failures += check_final(sc["final"], observed)
    except Exception as exc:  # a harness/system crash is a scenario failure, never silently skipped
        error = f"{type(exc).__name__}: {exc}"
    finally:
        h.clear_faults()
    for sr in step_results:
        failures += [f"step {sr.index} ({sr.say[:40]!r}): {f}" for f in sr.failures]
    if error:
        failures.append(f"crash: {error}")
    return {
        "id": sc["id"], "metric": sc["metric"], "passed": not failures, "failures": failures,
        "escalation": sc.get("escalation"),
        "predicted_escalation": any(s.outcome in ESCALATED_OUTCOMES for s in step_results),
        "steps": [vars(s) for s in step_results], "tool_calls": tool_calls,
        "seconds": round(time.monotonic() - started, 2),
    }


def run(dataset_path: Path, out: Path | None, only: set[str] | None, allow: bool) -> dict:
    db_name = _guard_database(allow)
    ds = json.loads(dataset_path.read_text())
    scenarios = [s for s in ds["scenarios"] if not only or s["id"] in only]
    h = Harness()
    results = [run_scenario(h, sc) for sc in scenarios]
    metrics = compute_metrics(results)
    from app.config import get_settings

    s = get_settings()
    report = {
        "dataset": {"path": str(dataset_path.relative_to(ROOT)), "version": ds["version"],
                    "note": ds.get("note", ""), "scenarios": len(scenarios)},
        "config": {"llm": "scripted/none (no real model)", "embedder": s.embedding_provider,
                   "vector_store": s.vector_store_provider, "database": db_name,
                   "agent_max_steps": s.agent_max_steps, "agent_max_tool_calls": s.agent_max_tool_calls},
        "run": {"git_sha": _git_sha(), "timestamp": datetime.now(UTC).isoformat(),
                "python": platform.python_version()},
        "metrics": metrics,
        "by_metric": {m: {"passed": sum(1 for r in results if r["metric"] == m and r["passed"]),
                          "total": sum(1 for r in results if r["metric"] == m)}
                      for m in sorted({r["metric"] for r in results})},
        "failures": [{"id": r["id"], "metric": r["metric"], "reasons": r["failures"]}
                     for r in results if not r["passed"]],
        "critical_failures": critical_failures(results),
        "scenarios": results,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, default=ROOT / "evals/agent/scenarios_v1.json")
    p.add_argument("--out", type=Path)
    p.add_argument("--only", help="comma-separated scenario ids")
    p.add_argument("--strict", action="store_true", help="exit non-zero on ANY scenario failure")
    p.add_argument("--allow-destructive", action="store_true")
    a = p.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import logging

    logging.disable(logging.INFO)  # per-request access logs would drown the report
    report = run(a.dataset.resolve(), a.out, set(a.only.split(",")) if a.only else None,
                 a.allow_destructive)
    m = report["metrics"]
    print(json.dumps({k: v for k, v in m.items() if k != "counts"}, indent=2))
    print(f"scenarios: {m['counts']['passed']}/{m['counts']['scenarios']} passed")
    for f in report["failures"]:
        print(f"FAIL {f['id']} [{f['metric']}]: " + "; ".join(f["reasons"])[:300])
    if report["critical_failures"]:
        print("CRITICAL failures:", ", ".join(report["critical_failures"]))
        return 2
    return 1 if (a.strict and report["failures"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
