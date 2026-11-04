"""Pydantic DTOs for the org module (API and public)."""

from __future__ import annotations

import unicodedata
import uuid
from datetime import time
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("invalid IANA time zone") from exc
    return value


TermKey = Literal[
    "customer", "customers", "technician", "technicians", "service", "services", "appointment", "appointments"
]


class BusinessSettings(BaseModel):
    """Typed settings (M02 settings table). Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")
    terminology: dict[TermKey, str] = Field(default_factory=dict)

    @field_validator("terminology")
    @classmethod
    def _terminology(cls, values: dict[TermKey, str]) -> dict[TermKey, str]:
        for value in values.values():
            if (
                not 1 <= len(value) <= 40
                or not value.strip()
                or any(c in "<>" or unicodedata.category(c).startswith("C") for c in value)
            ):
                raise ValueError("terminology values must be 1–40 characters of plain, single-line text")
        return values

    hold_minutes: int = Field(default=30, ge=5, le=240)
    slot_granularity_minutes: Literal[5, 10, 15, 30] = 15
    booking_lead_time_minutes: int = Field(default=60, ge=0, le=10080)
    allow_self_approval: bool = False
    always_review_action_types: list[str] = Field(default_factory=list)
    allow_backorders: bool = False
    ai_policy: Literal["hosted_vultr", "hosted_any", "strict_private"] = "hosted_vultr"
    ai_daily_token_budget: int | None = Field(default=None, ge=0)
    retention_days_conversations: int = Field(default=730, ge=30)
    public_chat_enabled: bool = True
    sales_tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1, max_digits=6, decimal_places=4)


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    legal_name: str | None
    timezone: str
    currency: str
    locale: str
    country: str | None
    public_profile: dict[str, Any]
    status: str
    version: int


class BusinessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    locale: str | None = Field(default=None, max_length=16)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    public_profile: dict[str, Any] | None = None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        return value if value is None else validate_timezone(value)


class BusinessBrief(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    timezone: str
    currency: str
    locale: str


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    address: dict[str, Any]
    timezone: str
    is_default: bool
    version: int


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    address: dict[str, Any] = Field(default_factory=dict)
    timezone: str | None = None
    is_default: bool = False

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        return value if value is None else validate_timezone(value)


class LocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: dict[str, Any] | None = None
    timezone: str | None = None
    is_default: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        return value if value is None else validate_timezone(value)


class OpeningHoursItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    opens: time
    closes: time


class OpeningHoursWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[OpeningHoursItem]


class SettingsOut(BaseModel):
    settings: BusinessSettings
    version: int


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    settings: BusinessSettings


class PublicProfileOut(BaseModel):
    avoid_medical_details: bool
    name: str
    timezone: str
    public_profile: dict[str, Any]
    opening_hours: list[OpeningHoursItem]
    ai_policy: Literal["hosted_vultr", "hosted_any", "strict_private"]  # drives the public privacy notice (M04 UI)
