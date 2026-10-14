import json

import httpx
import pytest

from app.channels import telegram, twilio
from app.channels.base import ChannelError, SendResult, chunk_text
from app.channels.delivery import backoff_seconds


def test_twilio_signature_matches_published_example():
    # Example from Twilio's webhook-security documentation.
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {"Digits": "1234", "To": "+18005551212", "From": "+14158675310",
              "Caller": "+14158675310", "CallSid": "CA1234567890ABCDE"}
    assert twilio.compute_signature("12345", url, params) == "GvWf1cFY/Q7PnoempGyD5oXAezc="


def test_twilio_signature_sorts_params_and_handles_repeated_values():
    a = twilio.compute_signature("t", "https://x/y", {"b": ["2"], "a": ["1", "0"]})
    b = twilio.compute_signature("t", "https://x/y", {"a": ["0", "1"], "b": ["2"]})
    assert a == b
    assert twilio.verify_signature("t", "https://x/y", {"b": ["2"], "a": ["0", "1"]}, a)


@pytest.mark.parametrize("sig,token,url", [
    (None, "t", "https://x/y"), ("", "t", "https://x/y"), ("bad", "t", "https://x/y"),
    ("anything", "", "https://x/y"),
])
def test_twilio_signature_rejections(sig, token, url):
    assert not twilio.verify_signature(token, url, {"a": ["1"]}, sig)


def test_twilio_signature_binds_url_and_body():
    good = twilio.compute_signature("t", "https://x/y", {"Body": ["hi"]})
    assert not twilio.verify_signature("t", "https://evil/y", {"Body": ["hi"]}, good)
    assert not twilio.verify_signature("t", "https://x/y", {"Body": ["hi!"]}, good)
    assert not twilio.verify_signature("other", "https://x/y", {"Body": ["hi"]}, good)


def form(**kw):
    base = {"MessageSid": "SM" + "a" * 32, "From": "whatsapp:+14155551234",
            "To": "whatsapp:+14155550100", "Body": "hello", "NumMedia": "0"}
    return {**base, **kw}


def test_twilio_parse_inbound():
    m = twilio.parse_inbound(form())
    assert (m.provider, m.sender, m.text, m.has_unsupported_media) == (
        "twilio_whatsapp", "whatsapp:+14155551234", "hello", False)
    media = twilio.parse_inbound(form(Body="", NumMedia="2"))
    assert media.text is None and media.has_unsupported_media


@pytest.mark.parametrize("bad", [
    form(MessageSid="nope"), form(From="+14155551234"), form(To="tel:123"),
    form(NumMedia="lots"), {k: v for k, v in form().items() if k != "MessageSid"},
])
def test_twilio_parse_inbound_rejects_bad_payloads(bad):
    with pytest.raises(ChannelError):
        twilio.parse_inbound(bad)


def test_twilio_sender_request_and_error_classification():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], req.content
        return httpx.Response(201, json={"sid": "SM123"})

    s = twilio.TwilioWhatsAppSender("AC1", "tok", "whatsapp:+1415", transport=httpx.MockTransport(handler))
    res = s.send("whatsapp:+1999", "Hello there")
    assert res.ok and res.provider_message_id == "SM123"
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json"
    assert seen["auth"].startswith("Basic ")
    assert b"Body=Hello+there" in seen["body"] and b"From=whatsapp%3A%2B1415" in seen["body"]

    def respond(status, **kw):
        t = httpx.MockTransport(lambda r: httpx.Response(status, **kw))
        return twilio.TwilioWhatsAppSender("AC1", "tok", "whatsapp:+1", transport=t).send("x", "y")

    r429 = respond(429, headers={"retry-after": "7"}, json={})
    assert not r429.ok and r429.retryable and r429.retry_after == 7
    assert respond(503).retryable
    bad = respond(400, json={"code": 21211, "message": "invalid number"})
    assert not bad.ok and not bad.retryable and "21211" in bad.error

    def boom(req):
        raise httpx.ConnectTimeout("slow")

    t = twilio.TwilioWhatsAppSender("AC1", "tok", "whatsapp:+1", transport=httpx.MockTransport(boom))
    assert t.send("x", "y").retryable


def test_telegram_secret_verification():
    assert telegram.verify_secret("abc", "abc")
    assert not telegram.verify_secret("abc", "abd")
    assert not telegram.verify_secret("abc", None) and not telegram.verify_secret("", "")


def update(**msg):
    return {"update_id": 5, "message": {"message_id": 1, "chat": {"id": 42, "type": "private"},
                                        **msg}}


def test_telegram_parse_update():
    m = telegram.parse_update(update(text=" hi "), "telegram")
    assert (m.event_id, m.sender, m.text) == ("5", "42", "hi")
    assert telegram.parse_update(update(text="/start"), "t").text == "hello"
    assert telegram.parse_update(update(photo=[{"file_id": "x"}]), "t").has_unsupported_media
    assert telegram.parse_update(update(photo=[{"file_id": "x"}]), "t").text is None
    assert telegram.parse_update({"update_id": 1, "edited_message": {}}, "t") is None
    group = update(text="hi")
    group["message"]["chat"]["type"] = "group"
    assert telegram.parse_update(group, "t") is None
    for bad in ({}, {"update_id": -1}, {"update_id": "x"}, {"update_id": 1, "message": {"chat": 5}}):
        with pytest.raises(ChannelError):
            telegram.parse_update(bad, "t")


def test_telegram_sender_classification_and_webhook_request():
    def send(status, body):
        t = httpx.MockTransport(lambda r: httpx.Response(status, json=body))
        return telegram.TelegramSender("123:abc", transport=t).send("42", "hi")

    ok = send(200, {"ok": True, "result": {"message_id": 9}})
    assert ok.ok and ok.provider_message_id == "9"
    limited = send(429, {"ok": False, "parameters": {"retry_after": 11}})
    assert limited.retryable and limited.retry_after == 11
    assert send(502, {}).retryable
    blocked = send(403, {"ok": False, "description": "bot was blocked by the user"})
    assert not blocked.ok and not blocked.retryable
    url, body = telegram.set_webhook_request("123:abc", "https://h/x", "sec")
    assert url.endswith("/bot123:abc/setWebhook") and body["secret_token"] == "sec"
    assert body["allowed_updates"] == ["message"]


def test_chunk_text():
    assert chunk_text("short", 100) == ["short"]
    long = ("Sentence number one is here. " * 80).strip()
    parts = chunk_text(long, 200)
    assert all(len(p) <= 200 for p in parts) and len(parts) > 1
    assert " ".join(parts).replace("  ", " ").split() == long.split()
    assert all(len(p) <= 50 for p in chunk_text("x" * 500, 50))
    assert chunk_text("a\n\nb", 100) == ["a\n\nb"]


def test_backoff_schedule_is_exponential_and_capped():
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_seconds(30) == 3600


def test_send_result_defaults():
    assert SendResult(True).retryable is False
    assert json.dumps({"ok": True})
