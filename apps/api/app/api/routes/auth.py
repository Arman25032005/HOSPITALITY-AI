from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.conversations.ratelimit import limiter
from app.core import metrics
from app.core.net import client_ip
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import AuditEvent, User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown, to keep timing similar.
_DUMMY_HASH = hash_password("timing-equalizer")


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db),
          settings: Settings = Depends(get_settings)) -> TokenResponse:
    email = body.email.strip().lower()
    ip = client_ip(request, settings)
    window = settings.login_rate_window_seconds
    # Per account+address and per address: slows credential stuffing without locking a real
    # user out from another network. Counted on every attempt, successful or not.
    if (not limiter.allow(f"login:{ip}:{email}", settings.login_rate_per_window, window)
            or not limiter.allow(f"login-ip:{ip}", settings.login_rate_per_window * 5, window)):
        metrics.RATE_LIMITED.labels("login").inc()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-in attempts",
                            headers={"Retry-After": str(window)})
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        if user:
            db.add(AuditEvent(organization_id=user.organization_id, actor_type="user",
                              actor_id=str(user.id), action="auth.login_failed",
                              entity_type="user", entity_id=str(user.id),
                              details={"reason": "inactive" if not user.is_active else "password"}))
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    user.last_login_at = datetime.now(UTC)
    db.add(AuditEvent(organization_id=user.organization_id, actor_type="user",
                      actor_id=str(user.id), action="auth.login", entity_type="user",
                      entity_id=str(user.id), details={}))
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id, user.organization_id,
                                                          user.role.value, user.token_version))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/refresh", response_model=TokenResponse)
def refresh(user: User = Depends(get_current_user)) -> TokenResponse:
    """Exchange a still-valid token for a fresh one (the user row is re-checked by the
    dependency, so deactivated accounts cannot extend their session)."""
    return TokenResponse(access_token=create_access_token(user.id, user.organization_id,
                                                          user.role.value, user.token_version))
