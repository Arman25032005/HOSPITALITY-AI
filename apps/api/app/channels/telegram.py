import hmac

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.channels.base import ChannelError, InboundMessage, SendResult

PROVIDER = "telegram"


def verify_secret(expected: str, header_value: str | None) -> bool:
    if not expected or not header_value:
        return False
    return hmac.compare_digest(expected.encode(), header_value.encode())


class _Chat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int
    type: str = "private"


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message_id: int
    chat: _Chat
    text: str | None = None
    caption: str | None = None
    photo: list | None = None
    voice: dict | None = None
    document: dict | None = None
    video: dict | None = None
    audio: dict | None = None
    sticker: dict | None = None
    location: dict | None = None
    contact: dict | None = None


class _Update(BaseModel):
    model_config = ConfigDict(extra="ignore")
    update_id: int = Field(ge=0)
    message: _Message | None = None


def parse_update(payload: dict, bot_id: str) -> InboundMessage | None:
    """Return None for update types we deliberately ignore (edits, callbacks, group chats)."""
    try:
        u = _Update.model_validate(payload)
    except ValidationError:
        raise ChannelError("Invalid Telegram update") from None
    m = u.message
    if m is None or m.chat.type != "private":
        return None
    media = any(getattr(m, f) for f in ("photo", "voice", "document", "video", "audio",
                                        "sticker", "location", "contact"))
    text = (m.text or "").strip()
    if text.startswith("/start"):
        text = "hello"
    return InboundMessage(PROVIDER, str(u.update_id), str(m.chat.id), bot_id, text or None, media)


class TelegramSender:
    provider = PROVIDER
    max_chars = 4000

    def __init__(self, bot_token: str, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(base_url=f"https://api.telegram.org/bot{bot_token}",
                                   timeout=timeout, transport=transport)

    def send(self, recipient: str, text: str) -> SendResult:
        try:
            r = self.client.post("/sendMessage", json={"chat_id": recipient, "text": text})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return SendResult(False, retryable=True, error=type(exc).__name__)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 200 and body.get("ok"):
            return SendResult(True, str(body.get("result", {}).get("message_id")))
        retry_after = (body.get("parameters") or {}).get("retry_after")
        retryable = r.status_code == 429 or r.status_code >= 500
        return SendResult(False, retryable=retryable, retry_after=retry_after,
                          error=f"HTTP {r.status_code}")


def set_webhook_request(bot_token: str, url: str, secret: str) -> tuple[str, dict]:
    """The setWebhook call that registers our URL and secret token (used by the setup script)."""
    return (f"https://api.telegram.org/bot{bot_token}/setWebhook",
            {"url": url, "secret_token": secret, "allowed_updates": ["message"],
             "drop_pending_updates": False})
