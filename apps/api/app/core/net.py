import ipaddress

from fastapi import Request

from app.config import Settings


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's address. X-Forwarded-For is honoured only when TRUST_PROXY_HEADERS is on,
    i.e. when the API sits behind a proxy that overwrites the header; otherwise any client could
    spoof it to dodge rate limits."""
    if settings.trust_proxy_headers:
        first = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"
