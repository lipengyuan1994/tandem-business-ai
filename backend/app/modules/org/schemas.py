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
    ai_autonomy_paused: bool = False
    outbound_send_delay_seconds: int = Field(default=120, ge=0, le=900)
    checkin_delay_days: int = Field(default=2, ge=0, le=365)
    customer_care_quiet_start_hour: int = Field(default=20, ge=0, le=23)
    customer_care_quiet_end_hour: int = Field(default=8, ge=0, le=23)
    customer_care_tone: Literal["warm", "formal", "concise"] = "warm"
    always_review_action_types: list[str] = Field(default_factory=list)
    allow_backorders: bool = False
    ai_policy: Literal["hosted_vultr", "hosted_any", "strict_private"] = "hosted_vultr"
    ai_daily_token_budget: int | None = Field(default=None, ge=0)
    retention_days_conversations: int = Field(default=730, ge=30)
    embed_allowed_origins: list[str] = Field(default_factory=list, max_length=10)
    embed_button_label: str = Field(default="Chat with us", min_length=1, max_length=80)
    self_service_enabled: bool = False
    cancellation_notice_hours: int = Field(default=24, ge=0, le=8760)
    cancellation_fee_text: str | None = Field(default=None, max_length=1000)

    @field_validator("embed_allowed_origins")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        from app.platform.config import get_settings

        from .embed import normalize_origin

        return list(dict.fromkeys(normalize_origin(v, development=get_settings().app_env == "dev") for v in values))

    @field_validator("embed_button_label")
    @classmethod
    def _embed_label(cls, value: str) -> str:
        if not value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("plain button label required")
        return value.strip()

    public_chat_enabled: bool = True
    emergency_handling: bool = False
    emergency_instructions: str = Field(
        default="Please contact the clinic immediately by phone.", min_length=1, max_length=2000
    )
    handoff_after_hours_text: str = Field(
        default="We're currently closed. A person will reply here when we're open.", min_length=1, max_length=1000
    )
    sales_tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1, max_digits=6, decimal_places=4)


class BusinessOut(BaseModel):
    synthetic_demo: bool = False
    synthetic_demo_banner: str | None = None
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
    public_profile: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Public contact/profile settings; optional review_url and booking_url must be absolute HTTP(S) URLs; "
            "avoid_medical_details uses minimal care templates."
        ),
    )

    @field_validator("public_profile", check_fields=False)
    @classmethod
    def _links(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        from urllib.parse import urlsplit

        if value:
            for key in ("review_url", "booking_url"):
                link = value.get(key)
                if link is not None and (
                    not isinstance(link, str)
                    or len(link) > 500
                    or urlsplit(link).scheme not in {"http", "https"}
                    or not urlsplit(link).hostname
                    or urlsplit(link).username
                    or any(c.isspace() or c in "<>" for c in link)
                ):
                    raise ValueError(f"{key} must be an absolute HTTP(S) URL without credentials")
        return value

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
    synthetic_demo_banner: str | None = None
    settings: BusinessSettings
    version: int


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    settings: BusinessSettings


class PublicProfileOut(BaseModel):
    synthetic_demo_banner: str | None = None
    emergency_handling: bool = False
    emergency_instructions: str = ""
    embed_button_label: str = "Chat with us"
    ai_greeting: str
    avoid_medical_details: bool
    name: str
    timezone: str
    public_profile: dict[str, Any]
    opening_hours: list[OpeningHoursItem]
    ai_policy: Literal["hosted_vultr", "hosted_any", "strict_private"]  # drives the public privacy notice (M04 UI)
