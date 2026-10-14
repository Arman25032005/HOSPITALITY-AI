import json
from typing import Any

from app.config import Settings
from app.rag.embeddings import ConfigError

# Only these settings may be supplied by the secrets store.
ALLOWED = {
    "app_secret_key", "database_url", "redis_url", "twilio_account_sid", "twilio_auth_token",
    "twilio_whatsapp_number", "telegram_bot_token", "telegram_webhook_secret", "pinecone_api_key",
    "llm_api_key", "metrics_token",
}


def load_secret_overrides(settings: Settings, client: Any = None) -> dict:
    """Fetch a JSON object of secrets from AWS Secrets Manager (SECRETS_PROVIDER=aws,
    SECRETS_ID=<name or ARN>) and return the allowed keys as setting overrides.

    Credentials come from the runtime (IAM role). Not exercised against real AWS in this repo's
    tests, only against a stub client."""
    if settings.secrets_provider != "aws":
        raise ConfigError(f"Unknown SECRETS_PROVIDER {settings.secrets_provider!r}")
    if not settings.secrets_id:
        raise ConfigError("SECRETS_PROVIDER=aws requires SECRETS_ID")
    if client is None:
        try:
            import boto3
        except ImportError as exc:
            raise ConfigError("SECRETS_PROVIDER=aws requires `pip install boto3`") from exc
        client = boto3.client("secretsmanager")
    try:
        raw = client.get_secret_value(SecretId=settings.secrets_id)["SecretString"]
        data = json.loads(raw)
    except Exception as exc:  # never include secret material in the error
        raise RuntimeError(f"Could not load secrets: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise RuntimeError("Secret must be a JSON object")
    return {k.lower(): v for k, v in data.items() if k.lower() in ALLOWED and isinstance(v, str)}
