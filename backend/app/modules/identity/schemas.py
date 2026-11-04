"""DTOs for identity (API and public)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["owner", "admin", "operator", "inventory_manager", "marketing_manager", "auditor"]


class UserBrief(BaseModel):
    id: uuid.UUID
    display_name: str


class MembershipBrief(BaseModel):
    business_id: uuid.UUID
    business_name: str
    business_slug: str
    roles: list[Role]


class MeOut(BaseModel):
    user: UserBrief
    email: str
    memberships: list[MembershipBrief]
    active_business_id: uuid.UUID | None
    active_business_timezone: str | None
    permissions: list[str]
    csrf_token: str
    policy_version: str


class SwitchBusinessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: uuid.UUID


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    email: str
    title: str | None
    status: str
    roles: list[Role]
    version: int


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    title: str | None = Field(default=None, max_length=120)
    status: Literal["active", "suspended", "removed"] | None = None


class RolesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[Role] = Field(min_length=1)


class InvitationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    roles: list[Role] = Field(min_length=1)


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    roles: list[Role]
    status: str
    expires_at: datetime
    temporary_password: str | None = Field(
        default=None, description="Shown once, only when no SMTP is configured; the user must change it at first login"
    )


class BootstrapResult(BaseModel):
    business_id: uuid.UUID
    owner_user_id: uuid.UUID
    owner_email: str
    temporary_password: str | None
