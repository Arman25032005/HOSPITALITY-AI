import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Role, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    unauth = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds is None:
        raise unauth
    try:
        payload = decode_access_token(creds.credentials)
        if payload.get("typ") == "guest":
            raise unauth
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauth from None
    if (user is None or not user.is_active or str(user.organization_id) != payload.get("org")
            or user.token_version != payload.get("ver", 0)):
        raise unauth
    return user


def require_roles(*roles: Role) -> Callable[[User], User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return checker


@dataclass
class GuestSession:
    guest_id: uuid.UUID
    org_id: uuid.UUID
    hotel_id: uuid.UUID
    conversation_id: uuid.UUID


def get_guest_session(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> GuestSession:
    unauth = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds is None:
        raise unauth
    try:
        p = decode_access_token(creds.credentials)
        if p.get("typ") != "guest":
            raise unauth
        return GuestSession(uuid.UUID(p["sub"]), uuid.UUID(p["org"]), uuid.UUID(p["hotel"]),
                            uuid.UUID(p["conv"]))
    except (jwt.PyJWTError, ValueError, KeyError):
        raise unauth from None
