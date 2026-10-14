"""Register this deployment's webhook with Telegram (run once per bot / when the URL changes).

Usage: python -m scripts.telegram_set_webhook <integration_id>
Needs TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET and PUBLIC_BASE_URL. Not exercised against the
live Telegram API in this repository's tests (request shape only)."""
import sys

import httpx

from app.channels.telegram import set_webhook_request
from app.config import get_settings


def main(integration_id: str) -> int:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_webhook_secret and s.public_base_url):
        print("Set TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET and PUBLIC_BASE_URL first.",
              file=sys.stderr)
        return 2
    url, body = set_webhook_request(
        s.telegram_bot_token, f"{s.public_base_url.rstrip('/')}/api/v1/webhooks/telegram/"
        f"{integration_id}", s.telegram_webhook_secret)
    r = httpx.post(url, json=body, timeout=15)
    print(r.status_code, r.json().get("description", r.text[:200]))
    return 0 if r.status_code == 200 and r.json().get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else 2)
