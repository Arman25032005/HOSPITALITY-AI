import uuid
from datetime import UTC, datetime, timedelta

import jwt
import bcrypt

from app.config import get_settings

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode()[:72], hashed.encode())


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_expire_minutes),
    }
    return jwt.encode(payload, s.app_secret_key, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.app_secret_key, algorithms=[s.jwt_algorithm])


def create_guest_token(guest_id: uuid.UUID, org_id: uuid.UUID, hotel_id: uuid.UUID,
                       conversation_id: uuid.UUID) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"typ": "guest", "sub": str(guest_id), "org": str(org_id), "hotel": str(hotel_id),
               "conv": str(conversation_id), "iat": now,
               "exp": now + timedelta(minutes=s.guest_token_expire_minutes)}
    return jwt.encode(payload, s.app_secret_key, algorithm=s.jwt_algorithm)
