from app.channels.base import SendResult


class MockSender:
    """Deterministic in-memory sender for tests and local development. Records every message and
    can be scripted to fail. Never talks to a provider; replies sent through it are NOT delivered."""

    def __init__(self, provider: str = "mock", max_chars: int = 1500):
        self.provider, self.max_chars = provider, max_chars
        self.sent: list[tuple[str, str]] = []
        self.results: list[SendResult] = []

    def send(self, recipient: str, text: str) -> SendResult:
        if self.results:
            res = self.results.pop(0)
            if not res.ok:
                return res
        self.sent.append((recipient, text))
        return SendResult(True, f"mock-{len(self.sent)}")
