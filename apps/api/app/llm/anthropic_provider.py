import anthropic
from pydantic import ValidationError

from app.llm.base import LLMInvalidOutput, LLMRefusal, LLMUnavailable, Usage


class AnthropicProvider:
    """Structured outputs via the official SDK (`messages.create` with a JSON-schema
    `output_config.format`, validated here so stop_reason can be checked first).

    Thinking stays at the model default; effort is set low because these calls are short
    classification/extraction tasks. Forced tool use is not used (rejected by current models).
    Refusals are surfaced as LLMRefusal and the caller falls back to deterministic handling,
    so server-side refusal fallbacks are intentionally not enabled here.
    """

    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 20.0,
                 client: anthropic.Anthropic | None = None):
        self.model = model
        self.timeout = timeout
        self.client = client or anthropic.Anthropic(api_key=api_key or None, max_retries=1,
                                                    timeout=timeout)

    def structured(self, *, system, messages, schema, max_tokens=1024):
        output_config = {"effort": "low", "format": {
            "type": "json_schema", "schema": anthropic.transform_schema(schema)}}
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system, messages=messages,
                output_config=output_config)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError,
                anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise LLMUnavailable(type(exc).__name__) from None
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 429:
                raise LLMUnavailable(f"HTTP {exc.status_code}") from None
            raise LLMInvalidOutput(f"HTTP {exc.status_code}") from None
        # Check stop_reason before reading content: a refusal or truncation has no valid JSON.
        if resp.stop_reason == "refusal":
            raise LLMRefusal("model declined")
        if resp.stop_reason == "max_tokens":
            raise LLMInvalidOutput("output truncated")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError:
            raise LLMInvalidOutput("response did not match the schema") from None
        return parsed, Usage(resp.usage.input_tokens, resp.usage.output_tokens)
