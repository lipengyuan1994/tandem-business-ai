from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.modules.crm.public import CustomerCreate

Status = Literal["held", "confirmed", "cancelled", "completed", "no_show"]


def _aware(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("timestamps must include a time zone (use UTC, e.g. 2026-11-10T17:00:00Z)")
    return v


class ResourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["staff", "room", "equipment"] = "staff"
    location_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    color: str | None = Field(default=None, max_length=16)


class ResourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    color: str | None = None
    status: Literal["active", "archived"] | None = None


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    kind: str
    location_id: uuid.UUID
    user_id: uuid.UUID | None
    color: str | None
    status: str
    version: int


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    start_time: time
    end_time: time
    valid_from: date | None = None
    valid_to: date | None = None


class ExceptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime
    kind: Literal["closed", "extra_open"]
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        return _aware(v)


class ExceptionOut(ExceptionIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class SlotQuery(BaseModel):
    service_id: uuid.UUID
    earliest: datetime
    latest: datetime
    resource_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    limit: int = Field(default=10, ge=1, le=100)


class SlotOut(BaseModel):
    starts_at: datetime
    ends_at: datetime
    resource_id: uuid.UUID
    resource_name: str
    local_label: str


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    customer_id: uuid.UUID
    service_id: uuid.UUID
    resource_id: uuid.UUID
    location_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    blocked_until: datetime
    timezone: str
    status: Status
    hold_expires_at: datetime | None
    proposal_id: uuid.UUID | None
    source: str
    notes: str | None
    cancelled_reason: str | None
    version: int
    customer_name: str = ""
    service_name: str = ""
    resource_name: str = ""


class HoldIn(BaseModel):
    """Staff hold while a booking proposal is reviewed (M06 rule 3)."""

    service_id: uuid.UUID
    resource_id: uuid.UUID
    customer_id: uuid.UUID
    starts_at: datetime


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: uuid.UUID
    service_id: uuid.UUID
    resource_id: uuid.UUID
    starts_at: datetime
    notes: str | None = Field(default=None, max_length=2000)
    hold_id: uuid.UUID | None = None
    override_hours: bool = False
    skip_parts: bool = False

    @field_validator("starts_at")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        return _aware(v)


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    reason: str = Field(default="", max_length=300)


class RescheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    starts_at: datetime
    resource_id: uuid.UUID | None = None
    override_hours: bool = False

    @field_validator("starts_at")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        return _aware(v)


class VersionIn(BaseModel):
    version: int


class HoldDto(BaseModel):
    hold_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    hold_expires_at: datetime


class ExistingCustomer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID


class NewCustomer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new: CustomerCreate


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: uuid.UUID
    body: str = Field(min_length=1, max_length=4000)


class BookAppointmentPayload(BaseModel):
    """scheduling.book_appointment proposal payload (see M06 executors)."""

    model_config = ConfigDict(extra="forbid")
    customer: ExistingCustomer | NewCustomer
    service_id: uuid.UUID
    resource_id: uuid.UUID
    starts_at: datetime
    hold_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)
    reply: ReplyIn | None = None

    @field_validator("starts_at")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        return _aware(v)


class ReschedulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: uuid.UUID
    appointment_version: int
    new_starts_at: datetime
    resource_id: uuid.UUID | None = None
    reply: ReplyIn | None = None

    @field_validator("new_starts_at")
    @classmethod
    def _require_timezone(cls, v: datetime) -> datetime:
        return _aware(v)


class CancelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: uuid.UUID
    appointment_version: int
    reason: str = Field(min_length=1, max_length=300)
    reply: ReplyIn | None = None


class PublicRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: uuid.UUID
    preferred: list[PreferredWindow] = Field(min_length=1, max_length=5)
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)
    consent_to_contact: bool = False

    @model_validator(mode="after")
    def _contact(self) -> PublicRequestIn:
        if not self.email and not self.phone:
            raise ValueError("an email or a phone number is required")
        return self


class PreferredWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    time_from: time
    time_to: time


class PublicRequestOut(BaseModel):
    reply_url: str
    received: bool
    message: str


PublicRequestIn.model_rebuild()
