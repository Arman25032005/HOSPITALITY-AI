import threading
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Any failure to obtain a valid structured result. Callers fall back to deterministic paths."""


class LLMUnavailable(LLMError):
    """Transient: timeout, connection, rate limit, 5xx, or open circuit."""


class LLMRefusal(LLMError):
    """The model declined the request."""


class LLMInvalidOutput(LLMError):
    """The model answered but not with schema-valid data."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class LLMProvider(Protocol):
    name: str
    model: str

    def structured(self, *, system: str, messages: list[dict], schema: type[T],
                   max_tokens: int = 1024) -> tuple[T, Usage]:
        """Return an instance of `schema`. Raises LLMError subclasses; never returns free text."""
        ...


class CircuitBreaker:
    """Opens after `threshold` consecutive transient failures; half-opens after `reset_after`s."""

    def __init__(self, threshold: int = 3, reset_after: float = 30.0, clock=time.monotonic):
        self.threshold, self.reset_after, self.clock = threshold, reset_after, clock
        self._failures, self._opened_at = 0, None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self.clock() - self._opened_at >= self.reset_after:
                self._opened_at = None  # half-open: let one probe through
                self._failures = self.threshold - 1
                return True
            return False

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures, self._opened_at = 0, None
            else:
                self._failures += 1
                if self._failures >= self.threshold:
                    self._opened_at = self.clock()


class ResilientLLM:
    """Wraps a provider with a circuit breaker; only transient failures trip it."""

    def __init__(self, inner: LLMProvider, breaker: CircuitBreaker | None = None):
        self.inner = inner
        self.breaker = breaker or CircuitBreaker()
        self.name, self.model = inner.name, inner.model

    def structured(self, *, system, messages, schema, max_tokens=1024):
        if not self.breaker.allow():
            raise LLMUnavailable("circuit open")
        try:
            out = self.inner.structured(system=system, messages=messages, schema=schema,
                                        max_tokens=max_tokens)
        except LLMUnavailable:
            self.breaker.record(False)
            raise
        except LLMError:
            self.breaker.record(True)  # the service is up; the output was the problem
            raise
        self.breaker.record(True)
        return out
