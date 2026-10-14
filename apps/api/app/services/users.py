import uuid
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import Department, Hotel, Role, StaffAssignment, User
from app.services import audit
from app.services.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailed

_WEAK_FRAGMENTS = ("password", "passw0rd", "qwerty", "letmein", "welcome", "changeme", "admin",
                   "123456", "abcdef", "iloveyou", "hotel123")


def validate_password(pw: str, *, email: str, full_name: str, min_length: int) -> None:
    low = pw.lower()
    if len(pw) < min_length:
        raise ValidationFailed(f"Password must be at least {min_length} characters")
    if any(f in low for f in _WEAK_FRAGMENTS) or len(set(pw)) < 5:
        raise ValidationFailed("That password is too easy to guess")
    local = email.split("@")[0].lower()
    if (len(local) >= 4 and local in low) or any(
            len(t) >= 4 and t.lower() in low for t in full_name.split()):
        raise ValidationFailed("Password must not contain your name or email")
    if pw.isalpha() or pw.isdigit():
        raise ValidationFailed("Use a mix of letters, numbers or symbols")


def _admin(actor: User) -> None:
    if actor.role != Role.admin:
        raise ForbiddenError("Only administrators can manage users")


def _assignments(db: Session, user_id) -> list[dict]:
    rows = db.execute(
        select(StaffAssignment.hotel_id, Hotel.name, StaffAssignment.department_id, Department.name)
        .join(Hotel, Hotel.id == StaffAssignment.hotel_id)
        .outerjoin(Department, Department.id == StaffAssignment.department_id)
        .where(StaffAssignment.user_id == user_id)).all()
    return [{"hotel_id": h, "hotel_name": hn, "department_id": d, "department_name": dn}
            for h, hn, d, dn in rows]


def list_users(db: Session, *, actor: User) -> list[tuple[User, list[dict]]]:
    _admin(actor)
    users = db.scalars(select(User).where(User.organization_id == actor.organization_id)
                       .order_by(User.full_name)).all()
    return [(u, _assignments(db, u.id)) for u in users]


def _set_assignments(db: Session, actor: User, user: User, items) -> None:
    for a in items:
        hotel = db.get(Hotel, a.hotel_id)
        if hotel is None or hotel.organization_id != actor.organization_id:
            raise NotFoundError("Hotel not found")
        if a.department_id is not None:
            dept = db.get(Department, a.department_id)
            if dept is None or dept.hotel_id != hotel.id:
                raise NotFoundError("Department not found")
    db.execute(delete(StaffAssignment).where(StaffAssignment.user_id == user.id))
    seen = set()
    for a in items:
        if (a.hotel_id, a.department_id) in seen:
            continue
        seen.add((a.hotel_id, a.department_id))
        db.add(StaffAssignment(organization_id=actor.organization_id, user_id=user.id,
                               hotel_id=a.hotel_id, department_id=a.department_id))


def create_user(db: Session, *, actor: User, email: str, full_name: str, role: str, password: str,
                assignments, min_password: int) -> User:
    _admin(actor)
    email = email.strip().lower()
    validate_password(password, email=email, full_name=full_name, min_length=min_password)
    if db.scalar(select(User.id).where(User.email == email)):
        raise ConflictError("A user with that email already exists")
    user = User(organization_id=actor.organization_id, email=email, full_name=full_name.strip(),
                password_hash=hash_password(password), role=Role(role), is_active=True)
    db.add(user)
    db.flush()
    _set_assignments(db, actor, user, assignments)
    audit.record(db, org_id=actor.organization_id, action="user.created", actor_type="user",
                 actor_id=str(actor.id), entity_type="user", entity_id=str(user.id),
                 details={"role": role})
    db.commit()
    return user


def _active_admins(db: Session, org_id) -> int:
    return db.scalar(select(func.count(User.id)).where(
        User.organization_id == org_id, User.role == Role.admin, User.is_active.is_(True))) or 0


def update_user(db: Session, *, actor: User, user_id: uuid.UUID, patch) -> User:
    _admin(actor)
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.organization_id != actor.organization_id:
        raise NotFoundError("User not found")
    changes: dict = {}
    if user.id == actor.id and (patch.role is not None or patch.is_active is not None):
        raise ForbiddenError("You cannot change your own role or active status")
    if patch.full_name is not None and patch.full_name.strip() != user.full_name:
        changes["full_name"] = [user.full_name, patch.full_name.strip()]
        user.full_name = patch.full_name.strip()
    bump = False
    if patch.role is not None and Role(patch.role) != user.role:
        if user.role == Role.admin and _active_admins(db, user.organization_id) <= 1:
            raise ConflictError("At least one active administrator must remain")
        changes["role"] = [user.role.value, patch.role]
        user.role = Role(patch.role)
        bump = True
    if patch.is_active is not None and patch.is_active != user.is_active:
        if (not patch.is_active and user.role == Role.admin
                and _active_admins(db, user.organization_id) <= 1):
            raise ConflictError("At least one active administrator must remain")
        changes["is_active"] = [user.is_active, patch.is_active]
        user.is_active = patch.is_active
        bump = True
    if patch.assignments is not None:
        _set_assignments(db, actor, user, patch.assignments)
        changes["assignments"] = len(patch.assignments)
    if bump:
        user.token_version += 1  # existing sessions stop working immediately
    if changes:
        audit.record(db, org_id=actor.organization_id, action="user.updated", actor_type="user",
                     actor_id=str(actor.id), entity_type="user", entity_id=str(user.id),
                     details={"changes": changes, "sessions_revoked": bump})
    db.commit()
    return user


def reset_password(db: Session, *, actor: User, user_id: uuid.UUID, new_password: str,
                   min_password: int) -> None:
    _admin(actor)
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or user.organization_id != actor.organization_id:
        raise NotFoundError("User not found")
    validate_password(new_password, email=user.email, full_name=user.full_name,
                      min_length=min_password)
    user.password_hash = hash_password(new_password)
    user.token_version += 1
    audit.record(db, org_id=actor.organization_id, action="user.password_reset",
                 actor_type="user", actor_id=str(actor.id), entity_type="user",
                 entity_id=str(user.id))
    db.commit()


def change_own_password(db: Session, *, user: User, current: str, new: str,
                        min_password: int) -> User:
    if not verify_password(current, user.password_hash):
        raise ForbiddenError("Current password is incorrect")
    if current == new:
        raise ValidationFailed("Choose a different password")
    validate_password(new, email=user.email, full_name=user.full_name, min_length=min_password)
    user.password_hash = hash_password(new)
    user.token_version += 1
    audit.record(db, org_id=user.organization_id, action="user.password_changed",
                 actor_type="user", actor_id=str(user.id), entity_type="user",
                 entity_id=str(user.id))
    db.commit()
    return user
