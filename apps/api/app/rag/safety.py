import re

# Retrieved passages are DATA. Passages that try to instruct the agent are quarantined at
# ingestion time and again at retrieval time. This is a heuristic screen, not a guarantee;
# prompts must also treat passages as untrusted quoted text.
_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any |the )?(previous|prior|above|system) ",
    r"you are (now )?(a|an|the) (?!guest)\w+ (assistant|agent|bot|ai)\b",
    r"\bsystem prompt\b",
    r"\b(reveal|print|show|output) (your|the) (instructions|prompt|rules|secrets?)\b",
    r"\bact as\b.{0,40}\b(admin|administrator|manager|developer)\b",
    r"\b(override|bypass|disable) (the )?(policy|policies|security|safety|approval|confirmation)",
    r"\bnew instructions?\s*:",
    r"<\s*/?\s*(system|assistant|tool)\s*>",
    r"\bapprove (all|every) (refund|request|booking)s?\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _PATTERNS]


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _COMPILED)
