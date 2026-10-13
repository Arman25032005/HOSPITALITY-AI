import re

_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")


def mask_cards(text: str) -> str:
    """Applied before storing any guest message: payment card numbers are never persisted."""
    return _CARD.sub("[card number removed]", text)


def redact_pii(text: str) -> str:
    """For logs, traces and tool-call records."""
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", mask_cards(text)))


def redact_args(obj):
    if isinstance(obj, str):
        return redact_pii(obj)
    if isinstance(obj, dict):
        return {k: redact_args(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_args(v) for v in obj]
    return obj
