"""Deterministic language understanding. High-precision rules first; the LLM (when configured)
is consulted only for what rules cannot decide, and its output is schema-validated."""
import re
from datetime import date, timedelta
from enum import Enum


class Intent(str, Enum):
    emergency = "emergency"
    human = "human"
    late_checkout = "late_checkout"
    cancel_reservation = "cancel_reservation"
    status = "status"
    service_request = "service_request"
    complaint = "complaint"
    reservation = "reservation"
    smalltalk = "smalltalk"
    knowledge = "knowledge"
    unknown = "unknown"


CODE_RE = re.compile(r"\b([RT])-([A-HJ-NP-Z2-9]{8})\b", re.I)
_QUESTION_START = re.compile(r"^(what|whats|what's|when|where|who|how|which|is|are|do|does|can i|"
                             r"could i|will|why)\b")

EMERGENCY_RE = re.compile(
    r"\b(on fire|there'?s a fire|fire (in|at|on|alarm|broke|is)|smell(ing)? smoke|see smoke|"
    r"smoke (in|from|is|coming)|gas leak|smell(ing)? gas|ambulance|heart attack|chest pain|"
    r"can'?t breathe|cannot breathe|not breathing|unconscious|bleeding|overdose|seizure|"
    r"choking|stabbed|shot|has a gun|weapon|assaulted|attacked|intruder|break-?in|call 911|"
    r"call the police|(this is|it'?s|we have|i have) an emergency|someone is (hurt|injured)|"
    r"i'?m (hurt|injured))\b", re.I)
HUMAN_RE = re.compile(
    r"\b((speak|talk|chat) (to|with) (a |an |the |your )?(real |human |live )?"
    r"(person|human|agent|manager|someone|staff|receptionist|representative|team)|"
    r"human (agent|please)|real person|connect me (to|with)|front desk please)\b", re.I)
LATE_CO_RE = re.compile(
    r"\b(late ?-?check ?-?out|check ?-?out (later|late)|extend (my )?check ?-?out|"
    r"(stay|keep (the|my) room|remain) (in the room )?(until|till|past|after) "
    r"(\d|noon|midday))\b", re.I)
CANCEL_RE = re.compile(r"\bcancel", re.I)
CANCEL_ACTION_RE = re.compile(
    r"^(please |hi,? |hello,? )?((i (want|need|would like|'d like|wish) to|i'?d like to|"
    r"can you|could you|please) )?cancel\b", re.I)
POLICYISH_RE = re.compile(r"\b(policy|policies|fee|fees|charge|penalt|refund|how (do|can|much)|"
                          r"what happens|free of charge|terms|rules?)\b", re.I)
STATUS_RE = re.compile(r"\b(status|update on|progress|any news|how long|when will|still waiting|"
                       r"has (it|my)|is (it|my) .* (done|approved|fixed)|check on)\b", re.I)
STATUS_SUBJECT_RE = re.compile(r"\b(ticket|request|reservation|booking|late ?-?check ?-?out|repair|"
                               r"maintenance|complaint|approval)\b", re.I)
PROBLEM_RE = re.compile(
    r"\b(broken|not working|doesn'?t work|isn'?t working|won'?t (work|turn|open|close|flush)|"
    r"leak(s|ing)?|dripping|flood(ed|ing)?|no (hot )?water|no heat|no power|too (hot|cold)|"
    r"locked out|stuck|clogged|blocked|burnt out|dead|dirty|stain(ed)?|needs? (cleaning|fixing)|"
    r"(a/?c|air ?-?con(ditioning)?|heater|heating|tv|television|toilet|shower|sink|light|"
    r"lamp|door|lock|key ?card|fridge|minibar|safe|kettle|phone|remote|curtain|window) "
    r"(is |are |has |have )?(broken|not|isn'?t|doesn'?t|won'?t|stopped|out|leak|noisy|stuck|dead))\b",
    re.I)
NEED_RE = re.compile(r"\b(need|send|bring|could (you|i)|can (you|i)|please|i'?d like|i would like|"
                     r"i want|request|require|get me|extra|more)\b", re.I)
ITEM_RE = re.compile(r"\b(towels?|pillows?|blankets?|toiletries|shampoo|soap|toothbrush|iron|"
                     r"hair ?dryer|cleaning|housekeeping|turndown|sheets|trash|amenities|"
                     r"slippers|robe|adapter|charger|crib|cot|ice|food|meal|dinner in|"
                     r"breakfast in|room service|taxi|cab|wake-?up call)\b", re.I)
COMPLAINT_RE = re.compile(
    r"\b(complain|complaint|unacceptable|rude|terrible|disgusting|awful|worst|disappointed|"
    r"outrageous|overcharged|double charged|refund|noisy neighbo|too noisy|bed ?bugs?|cockroach|"
    r"mold|stolen|theft|robbed|unsafe|harass|discriminat|insulted|ripped off|scam)\b", re.I)
RESERVE_RE = re.compile(
    r"\b(book|reserve|make a (reservation|booking)|looking for a (room|suite)|"
    r"(need|want|like) (a|an|to book) (room|suite)|rooms? available|availability|"
    r"any (rooms?|vacanc)|do you have (any )?(rooms?|suites?|availability|vacanc)|"
    r"new (reservation|booking))\b", re.I)
SMALLTALK_RE = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|"
                          r"thx|cheers|bye|goodbye|ok thanks|great thanks)\b[\s!.,]*$", re.I)
YES_RE = re.compile(r"^(yes|y|yep|yeah|yup|confirm(ed)?|ok(ay)?|sure|please do|go ahead|do it|"
                    r"book it|that'?s (right|correct|fine)|correct|proceed|yes please|"
                    r"please confirm|confirm it)[\s!.]*$", re.I)
NO_RE = re.compile(r"^(no|n|nope|nah|cancel( that| it)?|don'?t|do not|stop|never ?mind|"
                   r"not now|no thanks|no thank you|abort)[\s!.]*$", re.I)


def classify_rules(text: str) -> Intent:
    t = " ".join(text.strip().split())
    low = t.lower()
    if EMERGENCY_RE.search(low):
        return Intent.emergency
    if HUMAN_RE.search(low):
        return Intent.human
    q = bool(_QUESTION_START.match(low))
    if LATE_CO_RE.search(low) and (STATUS_RE.search(low) or re.search(r"\bstatus\b", low)):
        return Intent.status
    if LATE_CO_RE.search(low) and not re.match(r"^(what|whats|what's|how much|when|where|is there)\b",
                                               low):
        return Intent.late_checkout
    if CANCEL_RE.search(low) and (CODE_RE.search(t) or re.search(r"\b(reservation|booking|stay)\b",
                                                                  low)):
        if CANCEL_ACTION_RE.match(low) and not POLICYISH_RE.search(low):
            return Intent.cancel_reservation
        return Intent.knowledge
    if (STATUS_RE.search(low) and STATUS_SUBJECT_RE.search(low)) or (
            CODE_RE.search(t) and re.search(r"\b(status|update|check)\b", low)):
        return Intent.status
    if COMPLAINT_RE.search(low):
        return Intent.complaint
    if PROBLEM_RE.search(low):
        return Intent.service_request
    if NEED_RE.search(low) and ITEM_RE.search(low) and not re.match(
            r"^(what|when|where|how|is|are|do you|does)\b", low):
        return Intent.service_request
    if RESERVE_RE.search(low):
        return Intent.reservation
    if SMALLTALK_RE.match(low):
        return Intent.smalltalk
    if q or "?" in low:
        return Intent.knowledge
    return Intent.unknown


def parse_yes_no(text: str) -> bool | None:
    t = " ".join(text.strip().split())
    if len(t.split()) > 6:
        return None
    if YES_RE.match(t):
        return True
    if NO_RE.match(t):
        return False
    return None


# ---------------------------------------------------------------- slots

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}(?:,?\s+(\d{{4}}))?\b", re.I)
MDY_RE = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I)
NIGHTS_RE = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven)\s+nights?\b", re.I)
ADULTS_RE = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six)\s+(adults?|guests?|people|"
                       r"persons?|pax)\b", re.I)


def _num(s: str) -> int | None:
    s = s.lower()
    if s.isdigit():
        return int(s)
    return _NUM_WORDS.get(s) or {"seven": 7}.get(s)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def extract_dates(text: str, today: date) -> list[date]:
    found: list[tuple[int, date]] = []
    for m in ISO_RE.finditer(text):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if d:
            found.append((m.start(), d))
    spans = [(m.start(), m.end()) for m in ISO_RE.finditer(text)]

    def free(m):
        return not any(s <= m.start() < e for s, e in spans)

    for rx, order in ((DMY_RE, "dmy"), (MDY_RE, "mdy")):
        for m in rx.finditer(text):
            if not free(m):
                continue
            day, mon, yr = ((m[1], m[2], m[3]) if order == "dmy" else (m[2], m[1], m[3]))
            month = _MONTHS[mon[:3].lower()]
            year = int(yr) if yr else today.year
            d = _safe_date(year, month, int(day))
            if d and not yr and d < today:
                d = _safe_date(year + 1, month, int(day))
            if d:
                found.append((m.start(), d))
                spans.append((m.start(), m.end()))
    low = text.lower()
    for word, delta in (("tomorrow", 1), ("today", 0), ("tonight", 0)):
        i = low.find(word)
        if i >= 0 and not any(s <= i < e for s, e in spans):
            found.append((i, today + timedelta(days=delta)))
    return [d for _, d in sorted(found)]


def extract_stay_slots(text: str, today: date) -> dict:
    out: dict = {}
    dates = extract_dates(text, today)
    nights = NIGHTS_RE.search(text)
    if len(dates) >= 2:
        out["check_in"], out["check_out"] = dates[0], dates[1]
    elif len(dates) == 1 and nights and _num(nights[1]):
        out["check_in"] = dates[0]
        out["check_out"] = dates[0] + timedelta(days=_num(nights[1]))
    elif len(dates) == 1:
        out["check_in"] = dates[0]
    a = ADULTS_RE.search(text)
    low = text.lower()
    if a and _num(a[1]):
        out["adults"] = _num(a[1])
    elif re.search(r"\b(just me|only me|myself|solo|alone)\b", low):
        out["adults"] = 1
    elif re.search(r"\b(a couple|the two of us|two of us|me and my (wife|husband|partner))\b", low):
        out["adults"] = 2
    return out


def extract_code(text: str, prefix: str | None = None) -> str | None:
    m = CODE_RE.search(text)
    if not m or (prefix and m[1].upper() != prefix):
        return None
    return f"{m[1].upper()}-{m[2].upper()}"


def extract_verification(text: str) -> tuple[str, str] | None:
    """'R-ABCD2345 Smith' / 'code R-... surname Smith' -> (code, last_name).

    Deliberately strict: a bare code inside a longer sentence ("please cancel my reservation
    R-...") is not a verification attempt, so it cannot burn attempts or match a stray word."""
    code = extract_code(text, "R")
    if not code:
        return None
    rest = CODE_RE.sub(" ", text)
    m = re.search(r"(?:last ?name|surname)\s*(?:is|:)?\s*([A-Za-z][A-Za-z'-]{1,40})", rest, re.I)
    if m:
        return code, m[1]
    words = re.findall(r"[A-Za-z][A-Za-z'-]{1,40}", rest)
    filler = {"my", "code", "is", "the", "and", "confirmation", "reservation", "booking",
              "number", "with", "for", "it", "its", "it's", "hi", "hello", "name", "i", "am"}
    names = [w for w in words if w.lower() not in filler]
    if 1 <= len(names) <= 2 and len(words) <= 4:
        return code, names[-1]
    return None


def extract_room_number(text: str) -> str | None:
    m = re.search(r"\broom\s*(?:number|no\.?|#)?\s*(\d{3,4}[a-z]?)\b", text, re.I)
    return m[1].upper() if m else None


_NOT_NAMES = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "sure", "stop", "cancel",
              "help", "human", "agent", "me", "myself", "none", "nothing"}


def extract_name(text: str) -> str | None:
    t = text.strip()
    m = re.match(r"^(?:my name is|it'?s|its|i am|i'm|name:?|under)\s+(.+)$", t, re.I)
    cand = (m[1] if m else t).strip(" .!")
    if cand.lower() in _NOT_NAMES or parse_yes_no(cand) is not None:
        return None
    if re.fullmatch(r"[A-Za-z][A-Za-z'’ -]{1,58}[A-Za-z]", cand) and 1 <= len(cand.split()) <= 4:
        return " ".join(w.capitalize() if w.islower() else w for w in cand.split())
    return None
