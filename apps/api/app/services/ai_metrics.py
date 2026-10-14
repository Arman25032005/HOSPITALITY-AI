"""AI observability and product KPIs, computed from stored records. Formulas are documented in
docs/KPIS.md. Personal data shown here is passed through redact_pii."""
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.conversations.redact import redact_pii
from app.models import (
    AgentExecution, AuditEvent, Conversation, GuestFeedback, Message, Role, ServiceRequest,
    ToolExecution, User,
)
from app.services.errors import ForbiddenError, ValidationFailed

RESOLVED = {"answered", "action_done"}
HANDED = {"escalated", "handoff"}
NEEDS_WORK = {"abstained", "fallback", "error", "needs_info", "action_pending"}


def _guard(user: User, days: int | None = None) -> datetime:
    if user.role not in (Role.admin, Role.manager):
        raise ForbiddenError("Limited to managers and administrators")
    if days is not None and not 1 <= days <= 90:
        raise ValidationFailed("days must be between 1 and 90")
    return datetime.now(UTC) - timedelta(days=days or 7)


def cost_usd(tokens_in: int, tokens_out: int, s: Settings) -> float:
    return round((tokens_in * s.llm_input_price_per_mtok
                  + tokens_out * s.llm_output_price_per_mtok) / 1_000_000, 6)


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo))


def _window(db: Session, user: User, hotel_id, since):
    return (select(AgentExecution, Conversation.channel)
            .join(Conversation, Conversation.id == AgentExecution.conversation_id)
            .where(AgentExecution.organization_id == user.organization_id,
                   AgentExecution.hotel_id == hotel_id, AgentExecution.created_at >= since,
                   Conversation.channel != "staff"))


def executions(db: Session, *, user: User, hotel_id, settings: Settings, days: int = 7,
               outcome: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    since = _guard(user, days)
    q = _window(db, user, hotel_id, since)
    if outcome:
        q = q.where(AgentExecution.outcome == outcome)
    rows = db.execute(q.order_by(AgentExecution.created_at.desc())
                      .limit(min(limit, 200)).offset(offset)).all()
    ids = [e.id for e, _ in rows]
    tools: dict[uuid.UUID, list] = defaultdict(list)
    for t in db.scalars(select(ToolExecution).where(ToolExecution.agent_execution_id.in_(ids))
                        .order_by(ToolExecution.created_at)):
        tools[t.agent_execution_id].append(
            {"tool": t.tool_name, "success": t.success, "error": t.error,
             "latency_ms": t.latency_ms, "arguments": t.arguments})
    return [{"id": e.id, "created_at": e.created_at, "channel": ch,
             "correlation_id": e.correlation_id, "intent": e.intent, "outcome": e.outcome,
             "steps": e.steps, "latency_ms": e.latency_ms, "llm_ms": e.llm_ms,
             "tokens_in": e.tokens_in, "tokens_out": e.tokens_out,
             "estimated_cost_usd": cost_usd(e.tokens_in, e.tokens_out, settings),
             "trace": e.trace, "tools": tools.get(e.id, [])} for e, ch in rows]


def tool_stats(db: Session, *, user: User, hotel_id, days: int = 7) -> list[dict]:
    since = _guard(user, days)
    rows = db.execute(
        select(ToolExecution.tool_name, func.count(ToolExecution.id),
               func.count(ToolExecution.id).filter(ToolExecution.success.is_(False)),
               func.avg(ToolExecution.latency_ms))
        .join(AgentExecution, AgentExecution.id == ToolExecution.agent_execution_id)
        .where(AgentExecution.organization_id == user.organization_id,
               AgentExecution.hotel_id == hotel_id, ToolExecution.created_at >= since)
        .group_by(ToolExecution.tool_name).order_by(ToolExecution.tool_name)).all()
    return [{"tool": n, "calls": c, "failures": f,
             "success_rate": round((c - f) / c, 3) if c else None,
             "avg_latency_ms": round(float(a), 1) if a is not None else None}
            for n, c, f, a in rows]


def _questions_for(db: Session, user: User, corr_ids: list[str]) -> dict[str, str]:
    """correlation id -> the guest message that started that turn (redacted)."""
    if not corr_ids:
        return {}
    replies = db.execute(select(Message.meta["correlation_id"].astext, Message.meta["reply_to"].astext,
                                Message.conversation_id)
                         .where(Message.organization_id == user.organization_id,
                                Message.role == "assistant",
                                Message.meta["correlation_id"].astext.in_(corr_ids))).all()
    by_reply = {(conv, rt): cid for cid, rt, conv in replies}
    out: dict[str, str] = {}
    if not by_reply:
        return out
    for m in db.scalars(select(Message).where(
            Message.organization_id == user.organization_id, Message.role == "user",
            Message.external_message_id.in_([rt for _, rt in by_reply]))):
        cid = by_reply.get((m.conversation_id, m.external_message_id))
        if cid:
            out[cid] = redact_pii(m.content)[:300]
    return out


def unanswered(db: Session, *, user: User, hotel_id, days: int = 7, limit: int = 50) -> list[dict]:
    since = _guard(user, days)
    rows = db.execute(_window(db, user, hotel_id, since).where(
        AgentExecution.outcome.in_(("abstained", "fallback", "error")))
        .order_by(AgentExecution.created_at.desc()).limit(min(limit, 200))).all()
    qs = _questions_for(db, user, [e.correlation_id for e, _ in rows])
    return [{"at": e.created_at, "intent": e.intent, "outcome": e.outcome,
             "question": qs.get(e.correlation_id), "conversation_id": e.conversation_id}
            for e, _ in rows]


def flagged(db: Session, *, user: User, hotel_id, days: int = 30, max_rating: int = 2) -> list[dict]:
    since = _guard(user, days)
    rows = db.execute(
        select(GuestFeedback, Message)
        .join(Message, Message.id == GuestFeedback.message_id)
        .where(GuestFeedback.organization_id == user.organization_id,
               GuestFeedback.hotel_id == hotel_id, GuestFeedback.rating <= max_rating,
               GuestFeedback.created_at >= since)
        .order_by(GuestFeedback.created_at.desc()).limit(100)).all()
    qs = _questions_for(db, user, [m.meta.get("correlation_id") for _, m in rows
                                   if m.meta.get("correlation_id")])
    return [{"at": f.created_at, "rating": f.rating, "comment": redact_pii(f.comment or "") or None,
             "answer": redact_pii(m.content)[:500], "question": qs.get(m.meta.get("correlation_id")),
             "outcome": m.meta.get("outcome"), "conversation_id": m.conversation_id}
            for f, m in rows]


def kpis(db: Session, *, user: User, hotel_id, settings: Settings, days: int = 7) -> dict:
    since = _guard(user, days)
    rows = db.execute(_window(db, user, hotel_id, since).order_by(
        AgentExecution.conversation_id, AgentExecution.created_at)).all()
    execs = [e for e, _ in rows]
    turns = len(execs)
    by_conv: dict[uuid.UUID, list[AgentExecution]] = defaultdict(list)
    for e in execs:
        by_conv[e.conversation_id].append(e)

    resolved = handed = unresolved = 0
    for seq in by_conv.values():
        outcomes = [e.outcome for e in seq]
        if any(o in HANDED for o in outcomes):
            handed += 1
        elif outcomes[-1] in RESOLVED:
            resolved += 1
        else:
            unresolved += 1
    convs = len(by_conv)
    knowledge_turns = [e for e in execs if e.intent == "knowledge"]
    abstained = sum(1 for e in knowledge_turns if e.outcome == "abstained")
    res_started = [c for c, seq in by_conv.items() if any(e.intent == "reservation" for e in seq)]
    res_done = [c for c in res_started if any(e.intent == "reservation" and e.outcome == "action_done"
                                              for e in by_conv[c])]
    lat = [e.latency_ms for e in execs if e.latency_ms is not None]
    llm_lat = [e.llm_ms for e in execs if e.tokens_in or e.tokens_out]  # turns that called a model
    tokens_in, tokens_out = sum(e.tokens_in for e in execs), sum(e.tokens_out for e in execs)
    cost = cost_usd(tokens_in, tokens_out, settings)

    tools = tool_stats(db, user=user, hotel_id=hotel_id, days=days)
    calls = sum(t["calls"] for t in tools)
    fails = sum(t["failures"] for t in tools)
    retr = [t for t in tools if t["tool"] == "search_knowledge"]

    created_ids = [uuid.UUID(i) for (i,) in db.execute(select(AuditEvent.entity_id).where(
        AuditEvent.organization_id == user.organization_id, AuditEvent.hotel_id == hotel_id,
        AuditEvent.action == "ticket.created", AuditEvent.actor_type == "agent",
        AuditEvent.created_at >= since))]
    done = 0
    if created_ids:
        done = db.scalar(select(func.count(ServiceRequest.id)).where(
            ServiceRequest.id.in_(created_ids),
            ServiceRequest.status.in_(("resolved", "closed")))) or 0
    fb = db.execute(select(func.avg(GuestFeedback.rating), func.count(GuestFeedback.id),
                           func.count(GuestFeedback.id).filter(GuestFeedback.rating <= 2)).where(
        GuestFeedback.organization_id == user.organization_id,
        GuestFeedback.hotel_id == hotel_id, GuestFeedback.created_at >= since)).one()

    def rate(n, d):
        return round(n / d, 3) if d else None

    outcomes = Counter(e.outcome for e in execs)
    return {
        "window_days": days, "turns": turns, "conversations": convs,
        "responded_conversations": convs,
        "resolved_conversations": resolved, "handed_off_conversations": handed,
        "unresolved_conversations": unresolved,
        "resolution_rate": rate(resolved, convs), "handoff_rate": rate(handed, convs),
        "turn_escalation_rate": rate(sum(outcomes[o] for o in HANDED), turns),
        "knowledge_abstain_rate": rate(abstained, len(knowledge_turns)),
        "reservation_flows_started": len(res_started),
        "reservation_completion_rate": rate(len(res_done), len(res_started)),
        "agent_tickets_created": len(created_ids),
        "agent_ticket_resolution_rate": rate(done, len(created_ids)),
        "tool_calls": calls, "tool_failures": fails, "tool_success_rate": rate(calls - fails, calls),
        "retrieval_avg_latency_ms": retr[0]["avg_latency_ms"] if retr else None,
        "turn_latency_p50_ms": _pct(lat, 0.5), "turn_latency_p95_ms": _pct(lat, 0.95),
        "model_latency_avg_ms": round(sum(llm_lat) / len(llm_lat)) if llm_lat else None,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "estimated_cost_usd": cost,
        "estimated_cost_per_conversation_usd": round(cost / convs, 6) if convs else None,
        "avg_feedback_rating": round(float(fb[0]), 2) if fb[0] is not None else None,
        "feedback_count": fb[1], "low_rated_answers": fb[2],
        "outcomes": dict(outcomes),
    }


def audit_log(db: Session, *, user: User, hotel_id, action: str | None = None,
              entity_type: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    _guard(user)
    q = (select(AuditEvent, User.full_name)
         .outerjoin(User, cast(User.id, String) == AuditEvent.actor_id)
         .where(AuditEvent.organization_id == user.organization_id,
                (AuditEvent.hotel_id == hotel_id) | AuditEvent.hotel_id.is_(None)))
    if action:
        q = q.where(AuditEvent.action.like(action.replace("%", "") + "%"))
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    rows = db.execute(q.order_by(AuditEvent.created_at.desc()).limit(min(limit, 500))
                      .offset(offset)).all()
    return [{"id": a.id, "at": a.created_at, "action": a.action, "actor_type": a.actor_type,
             "actor_name": n if a.actor_type == "user" else None, "entity_type": a.entity_type,
             "entity_id": a.entity_id, "details": a.details} for a, n in rows]
