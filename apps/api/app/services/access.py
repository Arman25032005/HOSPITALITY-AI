import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Department, Hotel, Role, StaffAssignment, User
from app.services.errors import ForbiddenError, NotFoundError

ORG_WIDE_ROLES = {Role.admin, Role.manager}


def get_hotel_for_user(db: Session, user: User, hotel_id: uuid.UUID) -> Hotel:
    """Return the hotel if it is in the user's org and the user may work there.

    Cross-tenant hotels are reported as not found so their existence is not leaked.
    """
    hotel = db.get(Hotel, hotel_id)
    if hotel is None or hotel.organization_id != user.organization_id:
        raise NotFoundError("Hotel not found")
    if user.role in ORG_WIDE_ROLES:
        return hotel
    assigned = db.scalar(
        select(StaffAssignment.id).where(
            StaffAssignment.user_id == user.id, StaffAssignment.hotel_id == hotel_id
        )
    )
    if assigned is None:
        raise NotFoundError("Hotel not found")
    return hotel


def user_department_ids(db: Session, user: User, hotel_id: uuid.UUID) -> set[uuid.UUID]:
    rows = db.scalars(
        select(StaffAssignment.department_id).where(
            StaffAssignment.user_id == user.id,
            StaffAssignment.hotel_id == hotel_id,
            StaffAssignment.department_id.is_not(None),
        )
    )
    return set(rows)


def department_by_code(db: Session, hotel_id: uuid.UUID, code: str) -> Department | None:
    return db.scalar(
        select(Department).where(Department.hotel_id == hotel_id, Department.code == code)
    )


def require_role(user: User, *roles: Role) -> None:
    if user.role not in roles:
        raise ForbiddenError("Insufficient permissions")
