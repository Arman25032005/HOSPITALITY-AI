import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.config import get_settings
from app.core.http_security import SecurityMiddleware
from app.core.observability import ObservabilityMiddleware, setup_logging
from app.core.tracing import setup_tracing
from app.services.errors import DomainError

settings = get_settings()
settings.validate_for_runtime()
setup_logging(settings.log_level, settings.log_json)
from app.conversations.ratelimit import limiter  # noqa: E402

limiter.configure(settings)
log = logging.getLogger("hospitality")

app = FastAPI(title="HospitalityOS AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityMiddleware, max_bytes=settings.max_request_bytes,
                   upload_max_bytes=settings.max_upload_bytes + 64 * 1024,
                   production=settings.environment == "production")
app.add_middleware(ObservabilityMiddleware)  # outermost: sees every response, incl. 413s
setup_tracing(app, settings)
app.include_router(api_router)


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint(request: Request, s=Depends(get_settings)) -> Response:
    """Prometheus scrape endpoint; protected by a bearer token when METRICS_TOKEN is set."""
    if s.metrics_token and request.headers.get("authorization") != f"Bearer {s.metrics_token}":
        raise HTTPException(401, "Unauthorized")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(DomainError)
async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse({"detail": "Internal server error"}, status_code=500)
