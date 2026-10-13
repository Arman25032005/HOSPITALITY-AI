"""Tool layer: the only way agent nodes touch application services.

Every call is argument-validated, actor-permission-checked, step-bounded, recorded in
tool_executions with redacted arguments, and refuses to repeat an identical failed call."""
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.agents.context import AgentContext
from app.conversations import service as convs
from app.conversations.redact import redact_args
from app.models import (
    ApprovalRequest, Guest, Reservation, ReservationStatus, ServiceRequest,
    TicketPriority, TicketStatus, ToolExecution,
)
from app.rag.answer import answer_question
from app.services import analytics, approvals
from app.services import reservations as resv
from app.services import tickets as tix
from app.services.errors import DomainError, ForbiddenError, NotFoundError

log = logging.getLogger("hospitality.agent")


class ToolBudgetExceeded(Exception):
    pass


@dataclass
class ToolResult:
    ok: bool
    value: Any = None
    error: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    args: type[BaseModel]
    fn: Callable[[AgentContext, Any], Any]
    actors: tuple[str, ...] = ("guest", "staff")


def run_tool(ctx: AgentContext, tool: Tool, raw_args: dict) -> ToolResult:
    if ctx.tool_calls >= ctx.settings.agent_max_tool_calls:
        raise ToolBudgetExceeded(tool.name)
    fingerprint = hashlib.sha256(
        f"{tool.name}:{json.dumps(raw_args, sort_keys=True, default=str)}".encode()).hexdigest()
    if fingerprint in ctx.failed_calls:
        return ToolResult(False, error="repeated failed call refused")
    ctx.tool_calls += 1
    started = time.monotonic()
    ok, err, value = False, "", None
    try:
        if ctx.actor not in tool.actors:
            raise ForbiddenError("Tool not permitted for this actor")
        args = tool.args.model_validate(raw_args)
        value = tool.fn(ctx, args)
        ok = True
    except ValidationError:
        err = "invalid tool arguments"
    except DomainError as exc:
        ctx.db.rollback()
        err = exc.message
    except Exception:
        ctx.db.rollback()
        log.exception("tool %s crashed", tool.name)
        err = "internal error"
    if not ok:
        ctx.failed_calls.add(fingerprint)
    try:
        ctx.db.add(ToolExecution(
            organization_id=ctx.org_id, agent_execution_id=ctx.execution.id, tool_name=tool.name,
            arguments=redact_args(json.loads(json.dumps(raw_args, default=str))), success=ok,
            error=err[:500] or None, latency_ms=int((time.monotonic() - started) * 1000)))
        ctx.db.commit()
    except Exception:
        ctx.db.rollback()
        log.exception("could not record tool execution")
    return ToolResult(ok, value, err)


# ------------------------------------------------------------------ helpers

def save_state(ctx: AgentContext) -> None:
    ctx.conv.state = ctx.state.dump()
    ctx.db.add(ctx.conv)
    ctx.db.commit()


def _link(lst: list[str], value) -> None:
    v = str(value)
    if v not in lst:
        lst.append(v)


def verified_reservations(ctx: AgentContext) -> list[Reservation]:
    out = []
    for rid in ctx.state.verified_reservation_ids:
        r = ctx.db.get(Reservation, uuid.UUID(rid))
        if r and r.organization_id == ctx.org_id and r.hotel_id == ctx.hotel_id:
            out.append(r)
    return out


def in_house_reservation(ctx: AgentContext) -> Reservation | None:
    for r in verified_reservations(ctx):
        if resv.is_in_house(r, ctx.today):
            return r
    return None


# ------------------------------------------------------------------ tools

class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


def _search_knowledge(ctx, a: Question):
    return answer_question(ctx.db, settings=ctx.settings, org_id=ctx.org_id,
                           hotel_id=ctx.hotel_id, question=a.question, audience="guest",
                           embedder=ctx.embedder, store=ctx.store)


class Stay(BaseModel):
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=10)


def _search_availability(ctx, a: Stay):
    return resv.search_availability(ctx.db, org_id=ctx.org_id, hotel_id=ctx.hotel_id,
                                    check_in=a.check_in, check_out=a.check_out, adults=a.adults)


class CreateReservation(Stay):
    room_type_id: uuid.UUID
    guest_name: str = Field(min_length=2, max_length=100)
    nonce: str


def _create_reservation(ctx, a: CreateReservation):
    guest = ctx.db.get(Guest, ctx.conv.guest_id)
    if guest is None or guest.organization_id != ctx.org_id:
        raise NotFoundError("Guest not found")
    guest.full_name = a.guest_name
    ctx.db.commit()
    res = resv.create_reservation(
        ctx.db, org_id=ctx.org_id, hotel_id=ctx.hotel_id, guest_id=guest.id,
        room_type_id=a.room_type_id, check_in=a.check_in, check_out=a.check_out, adults=a.adults,
        idempotency_key=f"chat:{ctx.conv.id}:{a.nonce}", actor_type="guest",
        actor_id=str(guest.id))
    # Source of truth: re-read what was persisted before telling the guest anything.
    ctx.db.expire_all()
    saved = ctx.db.get(Reservation, res.id)
    if saved is None or saved.status != ReservationStatus.confirmed:
        raise DomainError("Reservation could not be verified after creation")
    ctx.facts.add(saved.confirmation_code)
    _link(ctx.state.linked_reservation_ids, saved.id)
    _link(ctx.state.verified_reservation_ids, saved.id)  # the guest who just booked owns it
    return saved


class ReservationRef(BaseModel):
    reservation_id: uuid.UUID


def _cancel_reservation(ctx, a: ReservationRef):
    if str(a.reservation_id) not in ctx.state.verified_reservation_ids:
        raise ForbiddenError("Reservation is not verified for this conversation")
    resv.cancel_reservation(ctx.db, org_id=ctx.org_id, hotel_id=ctx.hotel_id,
                            reservation_id=a.reservation_id, actor_type="guest",
                            actor_id=str(ctx.conv.guest_id))
    ctx.db.expire_all()
    saved = ctx.db.get(Reservation, a.reservation_id)
    if saved is None or saved.status != ReservationStatus.cancelled:
        raise DomainError("Cancellation could not be verified")
    ctx.facts.add(saved.confirmation_code)
    return saved


class Verify(BaseModel):
    code: str = Field(pattern=r"^R-[A-HJ-NP-Z2-9]{8}$")
    last_name: str = Field(min_length=1, max_length=60)


def _verify_stay(ctx, a: Verify):
    res = convs.apply_verification(ctx.db, ctx.state, org_id=ctx.org_id, hotel_id=ctx.hotel_id,
                                   code=a.code, last_name=a.last_name)
    save_state(ctx)
    if res is not None:
        ctx.facts.add(res.confirmation_code)
    return res


class NewTicket(BaseModel):
    category: Literal["maintenance", "housekeeping", "room_service", "concierge"]
    description: str = Field(min_length=3, max_length=2000)
    priority: TicketPriority = TicketPriority.normal
    room_number: str | None = Field(default=None, max_length=20)


def _create_ticket(ctx, a: NewTicket):
    res = in_house_reservation(ctx)
    if res is None:
        raise ForbiddenError("A verified current stay is required")
    # Repeat requests (guest re-sending, webhook retry) reuse the open ticket.
    cutoff = time.time() - 15 * 60
    for tid in ctx.state.linked_ticket_ids:
        t = ctx.db.get(ServiceRequest, uuid.UUID(tid))
        if (t and t.category == a.category and t.reservation_id == res.id
                and t.status in (TicketStatus.open, TicketStatus.assigned, TicketStatus.in_progress)
                and t.created_at.timestamp() >= cutoff):
            ctx.facts.add(t.ticket_number)
            return t, False
    room = resv.room_number_for(ctx.db, res)
    notes = [] if room else ["Room number was provided by the guest and is not verified"]
    t = tix.create_ticket(
        ctx.db, org_id=ctx.org_id, hotel_id=ctx.hotel_id, category=a.category,
        description=a.description, priority=a.priority, guest_id=res.guest_id,
        room_number=room or a.room_number, actor_type="agent", actor_id=ctx.correlation_id,
        reservation_id=res.id, notes=notes)
    ctx.db.expire_all()
    saved = ctx.db.get(ServiceRequest, t.id)
    ctx.facts.add(saved.ticket_number)
    _link(ctx.state.linked_ticket_ids, saved.id)
    return saved, True


class Complaint(BaseModel):
    category: Literal["complaint", "emergency", "handoff"]
    description: str = Field(min_length=3, max_length=4000)
    priority: TicketPriority
    reason: str = Field(max_length=300)
    escalate: bool = True


def _create_escalated(ctx, a: Complaint):
    reservation_id = guest_id = room = None
    stays = verified_reservations(ctx)
    if stays:
        reservation_id, guest_id = stays[0].id, stays[0].guest_id
        room = resv.room_number_for(ctx.db, stays[0])
    guest_id = guest_id or ctx.conv.guest_id
    status = TicketStatus.escalated if a.escalate else TicketStatus.open
    t = tix.create_ticket(
        ctx.db, org_id=ctx.org_id, hotel_id=ctx.hotel_id, category=a.category,
        description=a.description, priority=a.priority, guest_id=guest_id, room_number=room,
        actor_type="agent", actor_id=ctx.correlation_id, reservation_id=reservation_id,
        initial_status=status, notes=[f"Escalation reason: {a.reason}"])
    ctx.db.expire_all()
    saved = ctx.db.get(ServiceRequest, t.id)
    ctx.facts.add(saved.ticket_number)
    _link(ctx.state.linked_ticket_ids, saved.id)
    return saved


class TicketLookup(BaseModel):
    ticket_number: str | None = Field(default=None, pattern=r"^T-[A-HJ-NP-Z2-9]{8}$")


def _ticket_status(ctx, a: TicketLookup):
    q = select(ServiceRequest).where(ServiceRequest.organization_id == ctx.org_id,
                                     ServiceRequest.hotel_id == ctx.hotel_id)
    verified = {uuid.UUID(r) for r in ctx.state.verified_reservation_ids}
    linked = {uuid.UUID(t) for t in ctx.state.linked_ticket_ids}
    if a.ticket_number:
        q = q.where(ServiceRequest.ticket_number == a.ticket_number)
    else:
        q = q.where(ServiceRequest.id.in_(linked)) if linked else q.where(False)
    out = []
    for t in ctx.db.scalars(q.order_by(ServiceRequest.created_at.desc()).limit(5)):
        if t.id in linked or (t.reservation_id and t.reservation_id in verified):
            ctx.facts.add(t.ticket_number)
            out.append(t)
    return out


class LateCheckout(BaseModel):
    reservation_id: uuid.UUID
    requested_until: str | None = Field(default=None, max_length=40)
    policy_excerpt: str = Field(default="", max_length=1500)


def _request_late_checkout(ctx, a: LateCheckout):
    res = in_house_reservation(ctx)
    if res is None or res.id != a.reservation_id:
        raise ForbiddenError("A verified current stay is required")
    arrivals = ctx.db.query(Reservation).filter(
        Reservation.hotel_id == ctx.hotel_id, Reservation.room_type_id == res.room_type_id,
        Reservation.check_in == res.check_out,
        Reservation.status == ReservationStatus.confirmed).count()
    ap, created = approvals.request_approval(
        ctx.db, org_id=ctx.org_id, hotel_id=ctx.hotel_id, kind="late_checkout",
        guest_id=res.guest_id, dedupe_key=str(res.id), actor_type="agent",
        actor_id=ctx.correlation_id,
        payload={"reservation_id": str(res.id), "confirmation_code": res.confirmation_code,
                 "check_out": res.check_out.isoformat(), "requested_until": a.requested_until,
                 "same_room_type_arrivals_on_checkout_day": arrivals,
                 "policy_excerpt": a.policy_excerpt[:1500]})
    _link(ctx.state.linked_approval_ids, ap.id)
    ctx.facts.add(res.confirmation_code)
    save_state(ctx)
    return ap, created


def _approval_status(ctx, a: BaseModel):
    out = []
    for aid in ctx.state.linked_approval_ids:
        ap = ctx.db.get(ApprovalRequest, uuid.UUID(aid))
        if ap and ap.organization_id == ctx.org_id and ap.hotel_id == ctx.hotel_id:
            out.append(ap)
    return out


class Empty(BaseModel):
    pass


class Days(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


def _analytics(ctx, a: Days):
    if ctx.user is None:
        raise ForbiddenError("Staff authentication required")
    return analytics.summary(ctx.db, user=ctx.user, hotel_id=ctx.hotel_id, days=a.days)


T_KNOWLEDGE = Tool("search_knowledge", Question, _search_knowledge)
T_AVAIL = Tool("search_availability", Stay, _search_availability)
T_CREATE_RES = Tool("create_reservation", CreateReservation, _create_reservation, ("guest",))
T_CANCEL_RES = Tool("cancel_reservation", ReservationRef, _cancel_reservation, ("guest",))
T_VERIFY = Tool("verify_stay", Verify, _verify_stay, ("guest",))
T_TICKET = Tool("create_service_ticket", NewTicket, _create_ticket, ("guest",))
T_ESCALATE = Tool("create_escalated_ticket", Complaint, _create_escalated, ("guest",))
T_TICKET_STATUS = Tool("get_ticket_status", TicketLookup, _ticket_status, ("guest",))
T_LATE_CO = Tool("request_late_checkout", LateCheckout, _request_late_checkout, ("guest",))
T_APPROVAL_STATUS = Tool("get_approval_status", Empty, _approval_status, ("guest",))
T_ANALYTICS = Tool("analytics_summary", Days, _analytics, ("staff",))
