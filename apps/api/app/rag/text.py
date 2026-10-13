import re

_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how i if in is it its me my of "
    "on or our please so that the their there this to us was we what when where which who will "
    "with you your am any about tell know hotel".split()
)
_NORMALIZE = [
    (re.compile(r"\bcheck[\s-]?in\b"), "checkin"),
    (re.compile(r"\bcheck[\s-]?out\b"), "checkout"),
    (re.compile(r"\bwi[\s-]?fi\b"), "wifi"),
    (re.compile(r"\bearly\s+checkin\b"), "early checkin"),
]
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


_SYNONYMS = {"booking": "reservation", "bookings": "reservation", "dog": "pet", "dogs": "pet",
             "cat": "pet", "cats": "pet", "internet": "wifi"}
_SUFFIXES = ("ations", "ation", "ingly", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es",
             "s", "e")


def _stem(w: str) -> str:
    """Light suffix stripper (not Porter). Applied identically to queries and documents."""
    w = _SYNONYMS.get(w, w)
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] in "lbdgmnprt":
        w = w[:-1]
    return w


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    t = text.lower()
    for pat, repl in _NORMALIZE:
        t = pat.sub(repl, t)
    words = [w.replace("'", "") for w in _WORD.findall(t)]
    if not keep_stopwords:
        words = [w for w in words if w not in _STOPWORDS]
    return [_stem(w) for w in words]


def normalize_text(text: str) -> str:
    """Strip control characters and collapse whitespace while preserving numbers/punctuation."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
