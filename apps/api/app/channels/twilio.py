import base64
import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence

import httpx

from app.channels.base import ChannelError, InboundMessage, SendResult

PROVIDER = "twilio_whatsapp"
_SID_RE = re.compile(r"^(SM|MM)[0-9a-fA-F]{32}$")
_NUM_RE = re.compile(r"^whatsapp:\+\d{6,15}$")


def compute_signature(auth_token: str, url: str, params: Mapping[str, Sequence[str] | str]) -> str:
    """Twilio request signature: HMAC-SHA1 over the full URL followed by each POST parameter
    (sorted by name; repeated names contribute each value) as name+value, base64 encoded."""
    data = url
    for key in sorted(params):
        vals = params[key]
        for v in ([vals] if isinstance(vals, str) else sorted(vals)):
            data += key + v
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_signature(auth_token: str, url: str, params: Mapping, signature: str | None) -> bool:
    if not signature or not auth_token:
        return False
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)


def parse_inbound(form: Mapping[str, str]) -> InboundMessage:
    sid = form.get("MessageSid") or form.get("SmsMessageSid") or ""
    sender, to = form.get("From", ""), form.get("To", "")
    if not _SID_RE.match(sid):
        raise ChannelError("Invalid MessageSid")
    if not _NUM_RE.match(sender) or not _NUM_RE.match(to):
        raise ChannelError("Invalid From/To")
    try:
        num_media = int(form.get("NumMedia", "0") or 0)
    except ValueError:
        raise ChannelError("Invalid NumMedia") from None
    body = (form.get("Body") or "").strip()
    return InboundMessage(PROVIDER, sid, sender, to, body or None, num_media > 0)


class TwilioWhatsAppSender:
    provider = PROVIDER
    max_chars = 1500  # WhatsApp via Twilio allows 1600; leave headroom

    def __init__(self, account_sid: str, auth_token: str, from_number: str, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None):
        self.from_number = from_number
        self.client = httpx.Client(
            base_url="https://api.twilio.com", auth=(account_sid, auth_token), timeout=timeout,
            transport=transport)
        self.account_sid = account_sid

    def send(self, recipient: str, text: str) -> SendResult:
        try:
            r = self.client.post(f"/2010-04-01/Accounts/{self.account_sid}/Messages.json",
                                 data={"To": recipient, "From": self.from_number, "Body": text})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return SendResult(False, retryable=True, error=type(exc).__name__)
        if r.status_code in (200, 201):
            try:
                return SendResult(True, r.json().get("sid"))
            except ValueError:
                return SendResult(True)
        retry_after = None
        try:
            retry_after = float(r.headers.get("retry-after", "")) or None
        except ValueError:
            pass
        retryable = r.status_code == 429 or r.status_code >= 500
        code = ""
        try:
            code = str(r.json().get("code", ""))
        except ValueError:
            pass
        return SendResult(False, retryable=retryable, retry_after=retry_after,
                          error=f"HTTP {r.status_code}" + (f" code {code}" if code else ""))
