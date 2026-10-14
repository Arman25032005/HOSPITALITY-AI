from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InboundMessage:
    provider: str  # twilio_whatsapp | telegram
    event_id: str  # provider-unique delivery id (idempotency key)
    sender: str  # stable external id of the guest on that channel
    recipient: str  # our number / bot id
    text: str | None  # None when the message has no usable text
    has_unsupported_media: bool = False


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    retryable: bool = False
    error: str | None = None
    retry_after: float | None = None


class Sender(Protocol):
    provider: str
    max_chars: int

    def send(self, recipient: str, text: str) -> SendResult: ...


class ChannelError(Exception):
    """Invalid or unauthenticated inbound payload."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def chunk_text(text: str, limit: int) -> list[str]:
    """Split on paragraph/sentence/space boundaries so each part fits the channel limit."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        while len(para) > limit:
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            piece, para = para[:cut], para[cut:].lstrip()
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(piece)
        if cur and len(cur) + len(para) + 1 > limit:
            parts.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]
