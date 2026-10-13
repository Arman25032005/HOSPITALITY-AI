import httpx
from pydantic import ValidationError

from app.llm.base import LLMInvalidOutput, LLMUnavailable, Usage


class OpenAICompatibleProvider:
    """Any server exposing POST {base_url}/chat/completions with JSON-schema response_format
    (hosted APIs, vLLM, Ollama, llama.cpp server). Uses plain HTTP, no vendor SDK.

    Not run against a live server in this repository's tests (mock transport only).
    """

    name = "openai-compatible"

    def __init__(self, model: str, base_url: str, api_key: str = "", timeout: float = 20.0,
                 transport: httpx.BaseTransport | None = None):
        self.model = model
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                   transport=transport,
                                   headers={"Authorization": f"Bearer {api_key}"} if api_key
                                   else {})

    def structured(self, *, system, messages, schema, max_tokens=1024):
        body = {
            "model": self.model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "system", "content": system}, *messages],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}},
        }
        try:
            r = self.client.post("/chat/completions", json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMUnavailable(type(exc).__name__) from None
        if r.status_code == 429 or r.status_code >= 500:
            raise LLMUnavailable(f"HTTP {r.status_code}")
        if r.status_code != 200:
            raise LLMInvalidOutput(f"HTTP {r.status_code}")
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            return (schema.model_validate_json(content),
                    Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            raise LLMInvalidOutput("response did not match the schema") from None
