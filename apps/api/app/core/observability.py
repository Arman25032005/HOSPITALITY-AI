import contextvars
import json
import logging
import re
import time
import uuid

from app.conversations.redact import redact_pii
from app.core import metrics

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_RID_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class PiiFilter(logging.Filter):
    """Redacts emails, phone numbers and card numbers from every log message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_pii(record.getMessage())
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
               "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "request_id": request_id_var.get()}
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                out[k] = v
        if record.exc_info:  # type + location only: tracebacks can echo user data
            et, ev, tb = record.exc_info
            out["exc_type"] = et.__name__ if et else None
            if tb:
                while tb.tb_next:
                    tb = tb.tb_next
                out["exc_at"] = f"{tb.tb_frame.f_code.co_filename.rsplit('/', 1)[-1]}:{tb.tb_lineno}"
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    handler.addFilter(PiiFilter())
    handler.addFilter(_RequestIdFilter())
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class ObservabilityMiddleware:
    """Pure ASGI: request id in/out, access log, and HTTP metrics keyed by route template."""

    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger("hospitality.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        rid = headers.get("x-request-id", "")
        rid = rid if _RID_OK.match(rid) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        state = {"status": 500}
        started = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            dur = time.perf_counter() - started
            method = scope["method"]
            metrics.HTTP_REQUESTS.labels(method, route, str(state["status"])).inc()
            metrics.HTTP_LATENCY.labels(method, route).observe(dur)
            if route != "/metrics" and route != "/health":
                self.log.info("request", extra={"method": method, "route": route,
                                                "status": state["status"],
                                                "duration_ms": int(dur * 1000)})
            request_id_var.reset(token)
