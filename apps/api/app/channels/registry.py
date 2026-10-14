from app.channels.base import Sender
from app.channels.telegram import TelegramSender
from app.channels.twilio import TwilioWhatsAppSender
from app.config import Settings

CHANNEL_FOR_PROVIDER = {"twilio_whatsapp": "whatsapp", "telegram": "telegram"}


def build_sender(settings: Settings, channel: str) -> Sender | None:
    """Real senders only when credentials are configured; otherwise None (never a fake)."""
    if channel == "whatsapp" and settings.twilio_account_sid and settings.twilio_auth_token \
            and settings.twilio_whatsapp_number:
        return TwilioWhatsAppSender(settings.twilio_account_sid, settings.twilio_auth_token,
                                    settings.twilio_whatsapp_number, settings.channel_timeout_seconds)
    if channel == "telegram" and settings.telegram_bot_token:
        return TelegramSender(settings.telegram_bot_token, settings.channel_timeout_seconds)
    return None
