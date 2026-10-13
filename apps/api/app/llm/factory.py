import os

from app.config import Settings
from app.llm.base import LLMProvider, ResilientLLM
from app.rag.embeddings import ConfigError


def get_llm(settings: Settings) -> LLMProvider | None:
    """Return the configured provider, or None for deterministic-only mode ("none")."""
    p = settings.llm_provider
    if p == "none":
        return None
    if p == "anthropic":
        key = (settings.llm_api_key or os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        if not key:
            raise ConfigError("LLM_PROVIDER=anthropic requires LLM_API_KEY or ANTHROPIC_API_KEY")
        from app.llm.anthropic_provider import AnthropicProvider

        return ResilientLLM(AnthropicProvider(settings.llm_model, key,
                                              settings.llm_timeout_seconds))
    if p == "openai-compatible":
        if not settings.llm_base_url:
            raise ConfigError("LLM_PROVIDER=openai-compatible requires LLM_BASE_URL")
        from app.llm.openai_compat import OpenAICompatibleProvider

        return ResilientLLM(OpenAICompatibleProvider(
            settings.llm_model, settings.llm_base_url, settings.llm_api_key,
            settings.llm_timeout_seconds))
    raise ConfigError(f"Unknown LLM_PROVIDER {p!r}")
