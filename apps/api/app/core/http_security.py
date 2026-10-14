import json

from app.core import metrics


class RequestTooLarge(BaseException):  # noqa: N818
    """BaseException on purpose: FastAPI's body parsing catches Exception and would turn this
    into a 400; BaseException passes through to this middleware, which answers 413."""


class SecurityMiddleware:
    """Pure ASGI: request size limits (declared and streamed) and baseline security headers."""

    def __init__(self, app, *, max_bytes: int, upload_max_bytes: int, production: bool):
        self.app, self.max_bytes, self.upload_max, self.production = (
            app, max_bytes, upload_max_bytes, production)

    def _limit_for(self, path: str) -> int:
        return self.upload_max if path.endswith("/knowledge/documents") else self.max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self._limit_for(scope["path"])
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        started = {"v": False}

        async def too_large():
            body = json.dumps({"detail": "Request body too large"}).encode()
            await send({"type": "http.response.start", "status": 413, "headers": self._headers(
                [(b"content-type", b"application/json"),
                 (b"content-length", str(len(body)).encode())])})
            await send({"type": "http.response.body", "body": body})

        try:
            if int(headers.get("content-length", "0") or 0) > limit:
                metrics.RATE_LIMITED.labels("body_size").inc()
                return await too_large()
        except ValueError:
            body = b'{"detail":"Invalid Content-Length"}'
            await send({"type": "http.response.start", "status": 400, "headers": self._headers(
                [(b"content-type", b"application/json"), (b"content-length", b"34")])})
            return await send({"type": "http.response.body", "body": body})

        seen = {"n": 0}

        async def limited_receive():
            msg = await receive()
            if msg["type"] == "http.request":
                seen["n"] += len(msg.get("body", b""))
                if seen["n"] > limit:
                    raise RequestTooLarge
            return msg

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                started["v"] = True
                message["headers"] = self._headers(list(message.get("headers", [])))
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except RequestTooLarge:
            if not started["v"]:
                metrics.RATE_LIMITED.labels("body_size").inc()
                await too_large()

    def _headers(self, headers: list) -> list:
        have = {k.lower() for k, _ in headers}
        extra = {
            b"x-content-type-options": b"nosniff", b"x-frame-options": b"DENY",
            b"referrer-policy": b"no-referrer", b"cache-control": b"no-store",
            b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
            b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
        }
        if self.production:
            extra[b"strict-transport-security"] = b"max-age=63072000; includeSubDomains"
        return headers + [(k, v) for k, v in extra.items() if k not in have]
