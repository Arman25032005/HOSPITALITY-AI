import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

StaffRole = Literal["admin", "manager", "receptionist", "department_staff"]


class Assignment(BaseModel):
    hotel_id: uuid.UUID
    department_id: uuid.UUID | None = None


class AssignmentOut(Assignment):
    hotel_name: str
    department_name: str | None = None


class UserAdminOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    assignments: list[AssignmentOut]


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(min_length=1, max_length=200)
    role: StaffRole
    password: str = Field(min_length=1, max_length=256)
    assignments: list[Assignment] = Field(default_factory=list, max_length=50)


class UserPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: StaffRole | None = None
    is_active: bool | None = None
    assignments: list[Assignment] | None = Field(default=None, max_length=50)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
