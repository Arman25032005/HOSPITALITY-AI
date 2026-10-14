import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User
from app.schemas.auth import TokenResponse
from app.schemas.users import (
    AssignmentOut, ChangePassword, PasswordReset, UserAdminOut, UserCreate, UserPatch,
)
from app.services import users as svc

router = APIRouter(prefix="/api/v1", tags=["users"])


def _out(u: User, assignments: list[dict]) -> UserAdminOut:
    return UserAdminOut(id=u.id, email=u.email, full_name=u.full_name, role=u.role.value,
                        is_active=u.is_active, last_login_at=u.last_login_at,
                        assignments=[AssignmentOut(**a) for a in assignments])


@router.get("/users", response_model=list[UserAdminOut])
def list_users(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_out(u, a) for u, a in svc.list_users(db, actor=actor)]


@router.post("/users", response_model=UserAdminOut, status_code=201)
def create_user(body: UserCreate, actor: User = Depends(get_current_user),
                db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    u = svc.create_user(db, actor=actor, email=body.email, full_name=body.full_name,
                        role=body.role, password=body.password, assignments=body.assignments,
                        min_password=settings.password_min_length)
    return _out(u, svc._assignments(db, u.id))


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def patch_user(user_id: uuid.UUID, body: UserPatch, actor: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    u = svc.update_user(db, actor=actor, user_id=user_id, patch=body)
    return _out(u, svc._assignments(db, u.id))


@router.post("/users/{user_id}/reset-password", status_code=204)
def reset_password(user_id: uuid.UUID, body: PasswordReset,
                   actor: User = Depends(get_current_user), db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)) -> None:
    svc.reset_password(db, actor=actor, user_id=user_id, new_password=body.new_password,
                       min_password=settings.password_min_length)


@router.post("/auth/change-password", response_model=TokenResponse)
def change_password(body: ChangePassword, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    u = svc.change_own_password(db, user=user, current=body.current_password,
                                new=body.new_password, min_password=settings.password_min_length)
    return TokenResponse(access_token=create_access_token(u.id, u.organization_id, u.role.value,
                                                          u.token_version))
