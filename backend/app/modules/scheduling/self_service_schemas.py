from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=320)


class LinkExchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class CustomerChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    fee_acknowledged: bool = False
    acknowledged_fee_text: str | None = Field(default=None, max_length=1000)


class CustomerReschedule(CustomerChange):
    starts_at: datetime
    resource_id: uuid.UUID

    @field_validator("starts_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("include time zone")
        return value


class CustomerBooking(BaseModel):
    id: uuid.UUID
    service_name: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    local_time: str
    location: str
    staff_name: str | None = None
    status: str
    version: int
    fee_required: bool
    fee_text: str | None


class CustomerBookings(BaseModel):
    items: list[CustomerBooking]


class CustomerSlot(BaseModel):
    starts_at: datetime
    ends_at: datetime
    resource_id: uuid.UUID
    local_label: str
