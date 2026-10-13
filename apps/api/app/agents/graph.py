"""LangGraph orchestration: typed state, explicit transitions, no cycles, bounded steps.

    gate -> (pending confirmation | classify) -> specialist node -> verify -> END

Specialist nodes live in flows.py. The ctx (db session, tools, limits) travels in
config["configurable"]; graph state only carries the message, intent and draft reply."""
import logging
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import flows
from app.agents.context import AgentContext, Reply
from app.agents.nlu import Intent, classify_rules, parse_yes_no
from app.agents.tools import ToolBudgetExceeded

log = logging.getLogger("hospitality.agent")


class AgentState(TypedDict, total=False):
    text: str
    route: str
    notice: str
    reply: Reply


def _ctx(config) -> AgentContext:
    return config["configurable"]["ctx"]


def node(name: str):
    """Wrap a node: count steps, trace, bound execution, and convert crashes to safe replies."""

    def deco(fn):
        def run(state: AgentState, config):
            ctx = _ctx(config)
            ctx.steps += 1
            if ctx.steps > ctx.settings.agent_max_steps:
                ctx.note(name, "step budget exceeded")
                return {"route": "verify",
                        "reply": Reply(flows.SAFE_FALLBACK, outcome="error")}
            try:
                out = fn(state, ctx) or {}
                ctx.note(name, out.get("route", "") or (out["reply"].outcome if "reply" in out else ""))
                return out
            except ToolBudgetExceeded as exc:
                ctx.note(name, f"tool budget exceeded at {exc}")
                ctx.db.rollback()
                return {"route": "verify", "reply": Reply(flows.SAFE_FALLBACK, outcome="error")}
            except Exception:
                log.exception("agent node %s failed", name)
                ctx.db.rollback()
                ctx.note(name, "crashed")
                return {"route": "verify", "reply": Reply(flows.SAFE_FALLBACK, outcome="error")}

        run.__name__ = name
        return run

    return deco


def _reply(r: Reply) -> dict:
    return {"reply": r, "route": "verify"}


@node("gate")
def gate(state: AgentState, ctx: AgentContext):
    text = state["text"]
    if ctx.actor == "staff":
        return {"route": "ops"}
    rules = classify_rules(text)
    if rules == Intent.emergency:
        return {"route": "emergency"}
    if ctx.conv.status == "handed_off":
        return {"route": "handed_off"}
    p = ctx.state.pending_action
    if p is not None:
        if p.expired():
            ctx.state.pending_action = None
            return {"route": "classify",
                    "notice": "Your earlier confirmation request expired, so nothing was done. "}
        if rules == Intent.human:
            return {"route": "classify"}
        yn = parse_yes_no(text)
        if yn is True:
            return {"route": "execute_pending"}
        if yn is False:
            return {"route": "decline_pending"}
        if rules in (Intent.knowledge, Intent.smalltalk):
            return {"route": "classify", "notice": "__remind__"}
        return {"route": "remind_pending"}
    return {"route": "classify"}


@node("classify")
def classify(state: AgentState, ctx: AgentContext):
    intent = flows.classify(ctx, state["text"])
    ctx.state.last_intent = intent.value
    ctx.execution.intent = intent.value
    return {"route": intent.value}


def _specialist(fn_name: str, *, with_text: bool = True):
    fn = getattr(flows, fn_name)

    @node(fn_name)
    def run(state: AgentState, ctx: AgentContext):
        reply = fn(ctx, state["text"]) if with_text else fn(ctx)
        notice = state.get("notice") or ""
        if notice == "__remind__" and ctx.state.pending_action is not None:
            reply.text += (f"\n\n(Reminder: I'm still waiting for your answer to: "
                           f"{ctx.state.pending_action.summary}. Reply YES or NO.)")
            reply.awaiting_confirmation = True
        elif notice and notice != "__remind__":
            reply.text = notice + reply.text
        return _reply(reply)

    return run


@node("execute_pending")
def execute_pending(state, ctx):
    return _reply(flows.execute_pending(ctx))


@node("decline_pending")
def decline_pending(state, ctx):
    return _reply(flows.decline_pending(ctx))


@node("remind_pending")
def remind_pending(state, ctx):
    return _reply(flows.remind_pending(ctx))


@node("handoff")
def handoff(state, ctx):
    return _reply(flows.handoff(ctx, "Guest asked to speak to a person"))


@node("handed_off")
def handed_off(state, ctx):
    return _reply(flows.handed_off_ack(ctx))


@node("verify")
def verify(state: AgentState, ctx: AgentContext):
    """Final guard: any confirmation code or ticket number in the reply must have been read
    back from the database during this turn; otherwise the reply is replaced."""
    reply = state["reply"]
    codes = set(re.findall(r"\b[RT]-[A-HJ-NP-Z2-9]{8}\b", reply.text))
    unverified = codes - ctx.facts
    if unverified:
        log.error("reply contained unverified identifiers %s", unverified)
        ctx.note("verify", "blocked unverified identifier")
        return {"reply": Reply(flows.SAFE_FALLBACK, outcome="error")}
    return {"reply": reply}


ROUTES = {
    "ops": "ops", "emergency": "emergency", "handed_off": "handed_off", "classify": "classify",
    "execute_pending": "execute_pending", "decline_pending": "decline_pending",
    "remind_pending": "remind_pending", "verify": "verify",
    Intent.human.value: "handoff", Intent.late_checkout.value: "late_checkout",
    Intent.cancel_reservation.value: "cancel_reservation", Intent.status.value: "status",
    Intent.service_request.value: "service_request", Intent.complaint.value: "complaint",
    Intent.reservation.value: "reservation", Intent.smalltalk.value: "smalltalk",
    Intent.knowledge.value: "knowledge", Intent.unknown.value: "knowledge",
}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("gate", gate)
    g.add_node("classify", classify)
    for name in ("knowledge", "reservation", "cancel_reservation", "service_request", "complaint",
                 "emergency", "late_checkout", "status", "smalltalk", "ops"):
        g.add_node(name, _specialist(name))
    for fn in (execute_pending, decline_pending, remind_pending, handoff, handed_off, verify):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "gate")
    for src in ("gate", "classify"):
        g.add_conditional_edges(src, lambda s: ROUTES[s["route"]])
    for name in ("knowledge", "reservation", "cancel_reservation", "service_request", "complaint",
                 "emergency", "late_checkout", "status", "smalltalk", "ops", "execute_pending",
                 "decline_pending", "remind_pending", "handoff", "handed_off"):
        g.add_edge(name, "verify")
    g.add_edge("verify", END)
    return g.compile()


GRAPH = build_graph()
