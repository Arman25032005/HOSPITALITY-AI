import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import Hotel, Role, StaffAssignment, User

router = APIRouter(prefix="/api/v1", tags=["hotels"])


class HotelOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    currency: str


@router.get("/hotels", response_model=list[HotelOut])
def my_hotels(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hotels this staff member may work in: all in the organisation for admins/managers,
    otherwise only assigned ones."""
    q = select(Hotel).where(Hotel.organization_id == user.organization_id)
    if user.role not in (Role.admin, Role.manager):
        q = q.where(Hotel.id.in_(select(StaffAssignment.hotel_id).where(
            StaffAssignment.user_id == user.id)))
    return list(db.scalars(q.order_by(Hotel.name)))
