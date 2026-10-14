"""Workflow implementations. Every user-visible fact comes from a tool result read back from the
database; wording is templated, never generated, so the model cannot invent policies, prices,
availability, or confirmation numbers."""
import re
from datetime import date, datetime, timedelta, UTC

from app.agents import tools as T
from app.agents.context import AgentContext, Reply
from app.agents.llm_tasks import llm_intent, llm_stay_slots
from app.agents.nlu import (
    Intent, classify_rules, extract_code, extract_name, extract_room_number, extract_stay_slots,
    extract_verification,
)
from app.conversations import service as convs
from app.conversations.state import PendingAction
from app.core import metrics
from app.models import Guest, ReservationStatus, TicketPriority
from app.rag.text import tokenize

CAPABILITIES = ("I can answer questions about the hotel, check room availability and make a "
                "reservation, log housekeeping or maintenance requests for your room, request a "
                "late checkout, and pass things to our team.")
VERIFY_FORMAT = "your confirmation code (it starts with R-), then your last name"
VERIFY_PROMPT = ("To help with your room I first need to verify your stay. Please send "
                 + VERIFY_FORMAT + ".")
SAFE_FALLBACK = ("I'm sorry, I couldn't complete that just now. I can connect you with our front "
                 "desk team if you'd like. Reply YES to be connected.")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _money(ctx: AgentContext, amount) -> str:
    return f"{amount:.2f} {ctx.hotel.currency}"


def _pending(ctx: AgentContext, kind: str, params: dict, summary: str) -> PendingAction:
    p = PendingAction(kind=kind, params=params, summary=summary,
                      expires_at=datetime.now(UTC) + timedelta(
                          minutes=ctx.settings.pending_confirmation_ttl_minutes))
    ctx.state.pending_action = p
    return p


def _offer_handoff(ctx: AgentContext, reply: Reply) -> Reply:
    if ctx.state.pending_action is None:
        _pending(ctx, "handoff", {"reason": "Guest accepted connection to staff"},
                 "connect you with our front desk team")
        reply.text += "\n\nWould you like me to connect you with our front desk team? Reply YES or NO."
        reply.awaiting_confirmation = True
    return reply


# ------------------------------------------------------------ classification

def classify(ctx: AgentContext, text: str) -> Intent:
    intent = classify_rules(text)
    flow_active = ctx.state.active_flow == "reservation"
    if flow_active and intent in (Intent.unknown, Intent.reservation, Intent.smalltalk):
        return Intent.reservation
    if intent == Intent.unknown:
        intent = llm_intent(ctx, text) or Intent.unknown
        if intent == Intent.unknown:
            return Intent.knowledge  # best effort; the answerer abstains when unsupported
    return intent


# ------------------------------------------------------------ knowledge

def knowledge(ctx: AgentContext, text: str) -> Reply:
    r = T.run_tool(ctx, T.T_KNOWLEDGE, {"question": text})
    if not r.ok:
        metrics.KB_ANSWERS.labels("error").inc()
        return _offer_handoff(ctx, Reply(SAFE_FALLBACK.split(" I can connect")[0], outcome="fallback"))
    ans = r.value
    metrics.KB_ANSWERS.labels("abstained" if ans.abstained else "answered").inc()
    if ans.abstained:
        ctx.state.consecutive_abstains += 1
        reply = Reply(ans.text, outcome="abstained")
        return _offer_handoff(ctx, reply) if ctx.state.consecutive_abstains >= 2 else reply
    ctx.state.consecutive_abstains = 0
    cites = [{"title": c.title, "version": c.version, "heading": c.heading,
              "document_id": str(c.document_id), "chunk_id": str(c.chunk_id)}
             for c in ans.citations]
    return Reply(ans.text, citations=cites)


# ------------------------------------------------------------ verification helper

def try_inline_verification(ctx: AgentContext, text: str) -> str | None:
    """If the message carries code + surname, verify. Returns an error reply text or None."""
    v = extract_verification(text)
    if not v or v[0] in {r.confirmation_code for r in T.verified_reservations(ctx)}:
        return None
    r = T.run_tool(ctx, T.T_VERIFY, {"code": v[0], "last_name": v[1]})
    if not r.ok:
        return "I can't verify stays for this conversation right now. Our front desk can help."
    if r.value is None:
        left = max(0, 5 - ctx.state.verify_failures)
        return ("I couldn't verify that confirmation code and last name"
                + (f" ({left} attempts left)." if left else ". Please contact the front desk."))
    return None


# ------------------------------------------------------------ reservation

def _pick_option(text: str, offered: dict[str, str]) -> tuple[str, str] | None:
    low = text.lower().strip()
    m = re.fullmatch(r"(?:option\s*)?(\d)\.?", low)
    ordinals = {"first": "1", "second": "2", "third": "3"}
    key = m[1] if m else next((v for k, v in ordinals.items() if re.search(rf"\b{k}\b", low)), None)
    if key and key in offered:
        return key, offered[key]
    for k in sorted((k for k in offered if k != "_for" and len(k) > 1), key=len, reverse=True):
        if k in low:
            return k, offered[k]
    return None


def reservation(ctx: AgentContext, text: str) -> Reply:
    d = ctx.state.draft
    ctx.state.active_flow = "reservation"
    guest = ctx.db.get(Guest, ctx.conv.guest_id)
    if not d.guest_name and guest and guest.full_name.strip().lower() != "guest":
        d.guest_name = guest.full_name

    slots = extract_stay_slots(text, ctx.today)
    if "check_in" not in slots and not d.check_in:
        slots.update({k: v for k, v in llm_stay_slots(ctx, text).items() if k not in slots})
    for k in ("check_in", "check_out"):
        if k in slots:
            setattr(d, k, slots[k].isoformat())
    if "check_in" in slots and "check_out" not in slots:
        d.check_out = d.check_out if d.check_out and d.check_out > d.check_in else None
    if "adults" in slots:
        d.adults = slots["adults"]

    # Selection and name capture come from the same message only when it carries no new dates.
    if d.offered and not d.room_type_id and not ({"check_in", "check_out"} & set(slots)):
        picked = _pick_option(text, d.offered)
        if picked:
            d.room_type_id = picked[1]
            d.room_type_name = next((k for k, v in d.offered.items()
                                     if v == picked[1] and not k.isdigit() and k not in
                                     ("cheapest", "_for") and len(k) > 4), picked[0]).title()
    elif d.room_type_id and not d.guest_name:
        name = extract_name(text)
        if name:
            d.guest_name = name

    try:
        ci = date.fromisoformat(d.check_in) if d.check_in else None
        co = date.fromisoformat(d.check_out) if d.check_out else None
    except ValueError:
        ci = co = None
    if ci and ci < ctx.today:
        d.check_in = d.check_out = None
        d.offered, d.room_type_id = {}, None
        return Reply("That check-in date is in the past. What dates would you like to stay "
                     "(for example 2026-11-03 to 2026-11-05)?", outcome="needs_info")
    if ci and co and (co <= ci or (co - ci).days > 30):
        d.check_out = None
        d.offered, d.room_type_id = {}, None
        return Reply("Check-out must be after check-in, and stays are limited to 30 nights. "
                     "What are your check-out date and number of nights?", outcome="needs_info")
    if not ci:
        return Reply("Happy to help you book. What are your check-in and check-out dates "
                     "(for example 2026-11-03 to 2026-11-05) and how many adults?",
                     outcome="needs_info")
    if not co:
        return Reply("Thanks. What is your check-out date (or how many nights)?",
                     outcome="needs_info")
    if not d.adults:
        return Reply("How many adults will be staying?", outcome="needs_info")

    stay_key = f"{d.check_in}|{d.check_out}|{d.adults}"
    if d.offered.get("_for") != stay_key:
        d.room_type_id = d.room_type_name = None
        r = T.run_tool(ctx, T.T_AVAIL, {"check_in": ci, "check_out": co, "adults": d.adults})
        if not r.ok:
            ctx.state.active_flow = None
            return _offer_handoff(ctx, Reply(r.error.capitalize() + ". " + SAFE_FALLBACK.split(
                "If you'd")[0], outcome="fallback"))
        opts = r.value
        nights = (co - ci).days
        if not opts:
            d.offered = {}
            d.check_in = d.check_out = None
            return Reply(f"Sorry, we have no rooms for {_plural(d.adults, 'adult')} from {ci} to {co}. "
                         "Would you like to try different dates?", outcome="needs_info")
        d.offered = {"_for": stay_key, "cheapest": str(opts[0].room_type_id)}
        lines = []
        for i, o in enumerate(opts, 1):
            d.offered[str(i)] = str(o.room_type_id)
            d.offered[o.name.lower()] = str(o.room_type_id)
            d.offered[o.code.lower()] = str(o.room_type_id)
            lines.append(f"{i}. {o.name} (sleeps {o.max_occupancy}): {_money(ctx, o.total_price)} "
                         f"for {_plural(nights, 'night')}")
        return Reply(f"Here is what's available from {ci} to {co} for {_plural(d.adults, 'adult')}:\n"
                     + "\n".join(lines) + "\nWhich room would you like? (name or number)",
                     outcome="needs_info")

    if not d.room_type_id:
        return Reply("Which room would you like? Please reply with the room name or its number.",
                     outcome="needs_info")
    if not d.guest_name:
        return Reply("What name should the reservation be under?", outcome="needs_info")

    r = T.run_tool(ctx, T.T_AVAIL, {"check_in": ci, "check_out": co, "adults": d.adults})
    match = [o for o in (r.value or []) if str(o.room_type_id) == d.room_type_id] if r.ok else []
    if not match:
        d.offered, d.room_type_id = {}, None
        return Reply("Sorry, that room is no longer available for those dates. Let me check "
                     "again: please tell me your dates and number of adults.", outcome="needs_info")
    o = match[0]
    nights = (co - ci).days
    summary = (f"{o.name}, {ci} to {co} ({_plural(nights, 'night')}), "
               f"{_plural(d.adults, 'adult')}, under the name "
               f"{d.guest_name}, total {_money(ctx, o.total_price)}")
    _pending(ctx, "create_reservation",
             {"check_in": d.check_in, "check_out": d.check_out, "adults": d.adults,
              "room_type_id": d.room_type_id, "guest_name": d.guest_name}, summary)
    return Reply(f"Please check: {summary}. No payment is taken online. Reply YES to confirm "
                 "this reservation or NO to cancel.", awaiting_confirmation=True,
                 outcome="action_pending")


# ------------------------------------------------------------ pending confirmations

def execute_pending(ctx: AgentContext) -> Reply:
    p = ctx.state.pending_action
    ctx.state.pending_action = None
    if p.kind == "create_reservation":
        r = T.run_tool(ctx, T.T_CREATE_RES, {**p.params, "nonce": p.nonce})
        if r.ok:
            res = r.value
            ctx.state.active_flow = None
            ctx.state.draft = type(ctx.state.draft)()
            return Reply(
                f"Your reservation is confirmed. Confirmation code: {res.confirmation_code}. "
                f"{p.summary}. Keep this code: with your last name it lets me verify your stay "
                "later.", reservation_codes=[res.confirmation_code], outcome="action_done")
        ctx.state.draft.offered, ctx.state.draft.room_type_id = {}, None
        if "sold out" in r.error.lower() or "not available" in r.error.lower():
            return Reply("Sorry, that room was just taken and is no longer available for those "
                         "dates. Nothing has been booked. Tell me your dates again to see what "
                         "else is available.", outcome="needs_info")
        return _offer_handoff(ctx, Reply("I couldn't complete the reservation, and nothing has "
                                         "been booked.", outcome="fallback"))
    if p.kind == "cancel_reservation":
        r = T.run_tool(ctx, T.T_CANCEL_RES, {"reservation_id": p.params["reservation_id"]})
        if r.ok:
            return Reply(f"Reservation {r.value.confirmation_code} is now cancelled. Any charges "
                         "that apply under the cancellation policy will be handled by the front "
                         "desk.", reservation_codes=[r.value.confirmation_code],
                         outcome="action_done")
        return _offer_handoff(ctx, Reply("I couldn't cancel that reservation, so it is unchanged.",
                                         outcome="fallback"))
    if p.kind == "handoff":
        return handoff(ctx, "Guest accepted an offer to speak to staff")
    return Reply(SAFE_FALLBACK, outcome="error")


def decline_pending(ctx: AgentContext) -> Reply:
    p = ctx.state.pending_action
    ctx.state.pending_action = None
    if p and p.kind == "create_reservation":
        ctx.state.draft.room_type_id = ctx.state.draft.room_type_name = None
        return Reply("No problem, I haven't booked anything. Tell me if you'd like different "
                     "dates or another room.", outcome="answered")
    return Reply("Okay, I won't go ahead with that.", outcome="answered")


def remind_pending(ctx: AgentContext) -> Reply:
    p = ctx.state.pending_action
    return Reply(f"I'm waiting for your answer: {p.summary}. Reply YES to go ahead or NO to "
                 "cancel.", awaiting_confirmation=True, outcome="action_pending")


# ------------------------------------------------------------ cancellation

def cancel_reservation(ctx: AgentContext, text: str) -> Reply:
    err = try_inline_verification(ctx, text)
    if err:
        return Reply(err, outcome="needs_info")
    code = extract_code(text, "R")
    stays = [r for r in T.verified_reservations(ctx)
             if r.status == ReservationStatus.confirmed and r.check_out >= ctx.today]
    if code:
        stays = [r for r in stays if r.confirmation_code == code]
    if not stays:
        return Reply("To cancel a reservation I need to verify it first. Please send your "
                     "confirmation code (it starts with R-) and last name "
                     "(only reservations that are still confirmed can be cancelled here).",
                     outcome="needs_info")
    if len(stays) > 1:
        return Reply("You have more than one reservation. Which confirmation code would you "
                     "like to cancel?", outcome="needs_info")
    res = stays[0]
    pol = T.run_tool(ctx, T.T_KNOWLEDGE, {"question": "cancellation policy"})
    policy = ""
    if pol.ok and not pol.value.abstained:
        policy = " Our policy: " + pol.value.text.split("\n\n")[0]
    summary = f"cancel reservation {res.confirmation_code} ({res.check_in} to {res.check_out})"
    _pending(ctx, "cancel_reservation", {"reservation_id": str(res.id)}, summary)
    ctx.facts.add(res.confirmation_code)
    return Reply(f"You asked to {summary}.{policy} Reply YES to cancel it or NO to keep it.",
                 awaiting_confirmation=True, outcome="action_pending")


# ------------------------------------------------------------ service requests

_CATEGORIES = [
    ("room_service", re.compile(r"\b(food|meal|dinner|breakfast in|room service|hungry|order)\b", re.I)),
    ("concierge", re.compile(r"\b(taxi|cab|wake-?up call|tickets?|restaurant booking|tour)\b", re.I)),
    ("housekeeping", re.compile(r"\b(towels?|pillows?|blankets?|toiletries|shampoo|soap|toothbrush|"
                                r"sheets|trash|clean(ing)?|housekeeping|turndown|dirty|stain(ed)?|"
                                r"slippers|robe|amenities|ice|crib|cot)\b", re.I)),
]
_HIGH_RE = re.compile(r"\b(flood(ed|ing)?|leak(s|ing)?|no (hot )?water|no heat|no power|"
                      r"locked out|sparks?|burning|stuck)\b", re.I)


def categorize_service(text: str) -> tuple[str, TicketPriority]:
    cat = next((c for c, rx in _CATEGORIES if rx.search(text)), "maintenance")
    return cat, TicketPriority.high if _HIGH_RE.search(text) else TicketPriority.normal


def service_request(ctx: AgentContext, text: str) -> Reply:
    err = try_inline_verification(ctx, text)
    if err:
        return Reply(err, outcome="needs_info")
    res = T.in_house_reservation(ctx)
    if res is None:
        if T.verified_reservations(ctx):
            return Reply("I can only log room requests during your stay, and I don't see a "
                         "current stay on the reservation you verified.", outcome="needs_info")
        return Reply(VERIFY_PROMPT, outcome="needs_info")
    cat, prio = categorize_service(text)
    r = T.run_tool(ctx, T.T_TICKET, {"category": cat, "description": text[:2000],
                                     "priority": prio.value,
                                     "room_number": extract_room_number(text)})
    if not r.ok:
        return _offer_handoff(ctx, Reply("I couldn't log that request just now.",
                                         outcome="fallback"))
    ticket, created = r.value
    label = cat.replace("_", " ")
    dept = "team"
    if created:
        text_out = (f"I've logged a {label} request (ticket {ticket.ticket_number}, priority "
                    f"{ticket.priority.value}) and routed it to our {label} {dept}. You can ask me "
                    "for its status at any time.")
    else:
        text_out = (f"You already have an open {label} request ({ticket.ticket_number}), so I "
                    "haven't created a duplicate. You can ask me for its status.")
    return Reply(text_out, ticket_numbers=[ticket.ticket_number], outcome="action_done")


# ------------------------------------------------------------ complaints, emergencies, handoff

_CRITICAL_RE = re.compile(r"\b(unsafe|assault|harass|discriminat|stolen|theft|robbed|injur|"
                          r"bed ?bugs?|cockroach|mold|threat)\w*", re.I)
_HIGH_COMPLAINT_RE = re.compile(r"\b(refund|overcharged|double charged|manager|unacceptable|"
                                r"outrageous|worst|rude|insulted|scam|ripped off)\b", re.I)


def complaint(ctx: AgentContext, text: str) -> Reply:
    if len(tokenize(text)) < 3:
        return Reply("I'm sorry to hear that. Could you tell me what happened so I can pass it "
                     "to the right person?", outcome="needs_info")
    if _CRITICAL_RE.search(text):
        prio, reason, esc = TicketPriority.urgent, "safety, security, or health-related complaint", True
    elif _HIGH_COMPLAINT_RE.search(text):
        prio, reason, esc = TicketPriority.high, "billing, service-failure or manager-request complaint", True
    else:
        prio, reason, esc = TicketPriority.normal, "general complaint", False
    r = T.run_tool(ctx, T.T_ESCALATE, {"category": "complaint", "description": text[:4000],
                                       "priority": prio.value, "reason": reason, "escalate": esc})
    if not r.ok:
        return _offer_handoff(ctx, Reply("I'm sorry, I couldn't record that just now.",
                                         outcome="fallback"))
    t = r.value
    who = "our duty management team" if esc else "our front desk team"
    return Reply(f"I'm sorry about this experience. I've recorded your complaint "
                 f"(reference {t.ticket_number}) and passed it to {who}. I can't promise a specific "
                 "outcome or compensation myself, but a team member will review it.",
                 ticket_numbers=[t.ticket_number],
                 outcome="escalated" if esc else "action_done")


def emergency(ctx: AgentContext, text: str) -> Reply:
    r = T.run_tool(ctx, T.T_ESCALATE, {
        "category": "emergency", "description": text[:4000], "priority": "urgent",
        "reason": "Possible emergency reported by guest", "escalate": True})
    ctx.conv.status = "handed_off"
    s = ctx.hotel.settings or {}
    instruction = s.get("emergency_instructions") or (
        "If anyone is in danger, call your local emergency number right now.")
    phone = s.get("front_desk_phone")
    msg = instruction + (f" You can also call the front desk on {phone}." if phone else "")
    tickets = []
    if r.ok:
        tickets = [r.value.ticket_number]
        msg += (f" I've flagged this to our staff as an urgent emergency (reference "
                f"{r.value.ticket_number}) and a team member will take over this conversation.")
    else:
        msg += " Please also tell the front desk directly."
    return Reply(msg, ticket_numbers=tickets, outcome="escalated")


def handoff(ctx: AgentContext, reason: str) -> Reply:
    recent = convs.history(ctx.db, ctx.conv.id, ctx.settings.context_window_messages)
    transcript = "\n".join(f"{m.role}: {m.content}" for m in recent)[-1800:]
    desc = f"{reason}.\n\nSummary: {ctx.conv.summary or 'n/a'}\n\nRecent conversation:\n{transcript}"
    r = T.run_tool(ctx, T.T_ESCALATE, {"category": "handoff", "description": desc[:4000],
                                       "priority": "normal", "reason": reason, "escalate": False})
    if not r.ok:
        return Reply("I couldn't pass this to the team just now. Please contact the front desk "
                     "directly.", outcome="fallback")
    ctx.conv.status = "handed_off"
    ctx.state.handoff_ticket = r.value.ticket_number
    ctx.state.pending_action = None
    return Reply(f"I've passed your conversation to our team (reference "
                 f"{r.value.ticket_number}), including what we've discussed so far. A team "
                 "member will follow up here. Meanwhile I won't make any changes.",
                 ticket_numbers=[r.value.ticket_number], outcome="handoff")


def handed_off_ack(ctx: AgentContext) -> Reply:
    ref = ctx.state.handoff_ticket
    ctx.facts.add(ref) if ref else None
    return Reply("Your conversation is with our team"
                 + (f" (reference {ref})" if ref else "")
                 + ". A team member will reply here. I can't make changes while they're helping.",
                 ticket_numbers=[ref] if ref else [], outcome="handoff")


# ------------------------------------------------------------ late checkout

_TIME_RE = re.compile(r"\b(noon|midday|\d{1,2}(?::\d{2})?\s?(?:am|pm))\b", re.I)


def late_checkout(ctx: AgentContext, text: str) -> Reply:
    pol = T.run_tool(ctx, T.T_KNOWLEDGE, {"question": "late checkout policy"})
    policy, cites = "", []
    if pol.ok and not pol.value.abstained:
        policy = pol.value.text.split("\n\n")[0]
        cites = [{"title": c.title, "version": c.version, "heading": c.heading,
                  "document_id": str(c.document_id), "chunk_id": str(c.chunk_id)}
                 for c in pol.value.citations[:1]]
    intro = f"Our late checkout policy: {policy}\n\n" if policy else ""
    err = try_inline_verification(ctx, text)
    if err:
        return Reply(intro + err, citations=cites, outcome="needs_info")
    res = T.in_house_reservation(ctx)
    if res is None:
        return Reply(intro + "To request a late checkout I need to verify your stay. Please send "
                     "your confirmation code (it starts with R-) and last name.",
                     citations=cites, outcome="needs_info")
    until = _TIME_RE.search(text)
    r = T.run_tool(ctx, T.T_LATE_CO, {"reservation_id": str(res.id),
                                      "requested_until": until[1] if until else None,
                                      "policy_excerpt": policy})
    if not r.ok:
        return _offer_handoff(ctx, Reply(intro + "I couldn't submit the request just now.",
                                         citations=cites, outcome="fallback"))
    _, created = r.value
    lead = ("I've asked the front desk to review a late checkout"
            if created else "You already have a late checkout request waiting with the front desk")
    return Reply(f"{intro}{lead} for reservation {res.confirmation_code}"
                 + (f" (until {until[1]})" if until else "") + ". It is NOT confirmed yet: only "
                 "staff can approve it, and I'll only tell you the outcome once they have "
                 "decided. Ask me for your late checkout status any time.",
                 citations=cites, reservation_codes=[res.confirmation_code],
                 outcome="action_pending")


# ------------------------------------------------------------ status

def status(ctx: AgentContext, text: str) -> Reply:
    err = try_inline_verification(ctx, text)
    if err:
        return Reply(err, outcome="needs_info")
    low = text.lower()
    lines: list[str] = []
    if re.search(r"late ?-?check|approval", low):
        r = T.run_tool(ctx, T.T_APPROVAL_STATUS, {})
        for ap in (r.value or []):
            code = ap.payload.get("confirmation_code", "")
            dec = (ap.payload.get("decision") or {})
            if ap.status == "pending":
                lines.append(f"Your late checkout request for {code} is still waiting for a "
                             "decision from the front desk.")
            elif ap.status == "approved":
                until = dec.get("approved_until")
                lines.append(f"Your late checkout for {code} was approved"
                             + (f" until {until}" if until else "") + "."
                             + (f" Note from staff: {dec['note']}" if dec.get("note") else ""))
            else:
                lines.append(f"Your late checkout request for {code} was not approved."
                             + (f" Note from staff: {dec['note']}" if dec.get("note") else ""))
            ctx.facts.add(code)
        if not lines:
            lines.append("I don't see a late checkout request in this conversation.")
    else:
        rcode = extract_code(text, "R")
        if rcode:
            mine = [x for x in T.verified_reservations(ctx) if x.confirmation_code == rcode]
            if mine:
                x = mine[0]
                ctx.facts.add(x.confirmation_code)
                lines.append(f"Reservation {x.confirmation_code}: {x.status.value}, "
                             f"{x.check_in} to {x.check_out}, {x.adults} adult(s).")
            else:
                lines.append("I can only share reservation details after verifying it: please "
                             "send the code and last name.")
        else:
            tcode = extract_code(text, "T")
            r = T.run_tool(ctx, T.T_TICKET_STATUS, {"ticket_number": tcode})
            for t in (r.value or []):
                lines.append(f"Ticket {t.ticket_number} ({t.category.replace('_', ' ')}): "
                             f"{t.status.value.replace('_', ' ')}, priority {t.priority.value}.")
            if not lines:
                lines.append("I can't find a matching request in this conversation. Send the "
                             "ticket number (T-...) or verify your stay with your confirmation "
                             "code and last name.")
    return Reply("\n".join(lines), outcome="answered")


def smalltalk(ctx: AgentContext, text: str) -> Reply:
    if re.search(r"\b(thanks|thank|thx|cheers)\b", text, re.I):
        return Reply("You're welcome! Is there anything else I can help with?")
    if re.search(r"\b(bye|goodbye)\b", text, re.I):
        return Reply("Goodbye, and thank you for contacting us.")
    return Reply(f"Hello, welcome to {ctx.hotel.name}! {CAPABILITIES}")


# ------------------------------------------------------------ staff analytics

_HELP_OPS = ("I can summarise operations for this hotel: open and overdue requests, escalations, "
             "complaint themes, average resolution time and reservations. Try: 'summary for the "
             "last 7 days' or 'overdue requests this month'.")


def ops(ctx: AgentContext, text: str) -> Reply:
    low = text.lower()
    days = 7
    m = re.search(r"(?:last|past)\s+(\d{1,2})\s+days?", low)
    if m:
        days = int(m[1])
    elif re.search(r"\b(today|24 hours)\b", low):
        days = 1
    elif re.search(r"\b(month|30 days)\b", low):
        days = 30
    elif re.search(r"\bweek\b", low):
        days = 7
    if not re.search(r"\b(summary|summar|report|overview|status|open|overdue|escalat|complain|"
                     r"reservation|resolution|metrics|how many|how are)\b", low):
        return Reply(_HELP_OPS)
    r = T.run_tool(ctx, T.T_ANALYTICS, {"days": days})
    if not r.ok:
        return Reply("You don't have access to analytics, or the request was invalid "
                     f"({r.error}).", outcome="fallback")
    s = r.value
    dept = ", ".join(f"{k}: {v}" for k, v in sorted(s["open_by_department"].items())) or "none"
    prio = ", ".join(f"{k}: {v}" for k, v in sorted(s["open_by_priority"].items())) or "none"
    themes = ", ".join(f"{k} {v}" for k, v in s["complaint_themes"].items() if v) or "none"
    res = (f"{s['avg_resolution_hours']} h" if s["avg_resolution_hours"] is not None
           else "no resolved tickets in window")
    text_out = (
        f"Operations summary, last {s['window_days']} day(s):\n"
        f"- Open requests now: {s['open_requests']} (by department: {dept}; by priority: {prio})\n"
        f"- Overdue now: {s['overdue']}\n"
        f"- Created in window: {s['created_in_window']}; escalations: {s['escalations_in_window']}\n"
        f"- Average resolution time (tickets resolved in window): {res}\n"
        f"- Complaints: {s['complaints_in_window']} (themes: {themes})\n"
        f"- Reservations: {s['active_reservations']} active today, {s['reservations_created']} "
        f"created and {s['reservations_cancelled']} cancelled in window")
    return Reply(text_out, data=s)
