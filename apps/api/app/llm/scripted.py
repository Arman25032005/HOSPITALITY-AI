from collections.abc import Callable

from app.llm.base import LLMError, Usage


class ScriptedLLM:
    """Test double. `script` maps (schema name, last user text) -> a schema instance or an
    exception to raise. Records every call so tests can assert on what was sent."""

    name, model = "scripted", "scripted-1"

    def __init__(self, script: Callable | None = None):
        self.script = script or (lambda schema, text: LLMError("no script"))
        self.calls: list[dict] = []

    def structured(self, *, system, messages, schema, max_tokens=1024):
        text = messages[-1]["content"] if messages else ""
        self.calls.append({"schema": schema.__name__, "system": system, "messages": messages})
        out = self.script(schema.__name__, text)
        if isinstance(out, Exception):
            raise out
        return out, Usage(10, 5)
