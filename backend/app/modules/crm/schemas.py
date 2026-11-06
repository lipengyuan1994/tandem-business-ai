from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from app.platform.errors import Invalid

ContactType = Literal["email", "phone"]
Channel = Literal["email", "sms"]
Purpose = Literal["marketing", "service_updates"]
ConsentStatusT = Literal["granted", "denied", "withdrawn"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize(kind: str, value: str) -> str:
    v = value.strip()
    if kind == "email":
        v = v.lower()
        if not EMAIL_RE.match(v):
            raise Invalid("crm.invalid_email")
        return v
    digits = re.sub(r"[\s().-]", "", v)
    if not re.fullmatch(r"\+?\d{7,15}", digits):
        raise Invalid("crm.invalid_phone")
    return digits if digits.startswith("+") else "+" + digits


class ContactPointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ContactType
    value: str = Field(min_length=3, max_length=320)
    is_primary: bool = False


class ContactPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    type: ContactType
    value: str
    is_primary: bool
    version: int


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Channel
    purpose: Purpose
    status: ConsentStatusT
    source: Literal["form", "staff", "import", "unsubscribe_link"] = "staff"
    evidence: str = Field(min_length=3, max_length=500)


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    channel: Channel
    purpose: Purpose
    status: ConsentStatusT
    source: str
    evidence: str
    recorded_at: datetime
    recorded_by: uuid.UUID | None


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["person", "organization"] = "person"
    display_name: str = Field(min_length=1, max_length=200)
    given_name: str | None = Field(default=None, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    organization_name: str | None = Field(default=None, max_length=200)
    # Optional in the API: omitted or null = email.
    preferred_contact_channel: Literal["email", "web_chat", "none"] | None = None
    preferred_language: str | None = Field(default=None, max_length=20)
    timezone: str | None = None
    contact_points: list[ContactPointIn] = Field(default_factory=list, max_length=10)
    consents: list[ConsentIn] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("display_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class CustomerFields(BaseModel):
    """Updatable fields (used by PATCH and by the crm.update_customer executor)."""

    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    given_name: str | None = Field(default=None, max_length=100)
    family_name: str | None = Field(default=None, max_length=100)
    organization_name: str | None = Field(default=None, max_length=200)
    # Omitted = unchanged; an explicit null is rejected (the column is not nullable).
    preferred_contact_channel: Literal["email", "web_chat", "none"] | None = None
    preferred_language: str | None = Field(default=None, max_length=20)
    timezone: str | None = None

    @field_validator("preferred_contact_channel", mode="before")
    @classmethod
    def _no_null_preference(cls, v: object) -> object:
        if v is None:
            raise ValueError("preferred_contact_channel cannot be null; omit it instead")
        return v

    @model_serializer(mode="wrap")
    def preserve_contact_preference_omission(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.preferred_contact_channel is None:
            data.pop("preferred_contact_channel", None)
        return data


class CustomerUpdate(CustomerFields):
    version: int


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: Literal["person", "organization"]
    display_name: str
    given_name: str | None
    family_name: str | None
    organization_name: str | None
    preferred_contact_channel: Literal["email", "web_chat", "none"] = "email"
    preferred_language: str | None
    timezone: str | None
    source: str
    status: str
    last_interaction_at: datetime | None
    created_at: datetime
    version: int


class CustomerListItem(CustomerOut):
    primary_email: str | None = None
    primary_phone: str | None = None
    tags: list[str] = Field(default_factory=list)


class CustomerDetail(CustomerOut):
    contact_points: list[ContactPointOut]
    tags: list[str]
    current_consents: list[ConsentOut]


class CustomerSummary(BaseModel):
    """What other modules (and the AI) may know: no raw contact values."""

    id: uuid.UUID
    display_name: str
    kind: str
    status: str
    contact_types: list[ContactType]
    tags: list[str]
    timezone: str | None
    last_interaction_at: datetime | None


class CustomerCandidate(BaseModel):
    customer_id: uuid.UUID
    display_name: str
    score: float
    reason: Literal["email", "phone", "name"]


class CareContact(BaseModel):
    """Internal delivery context. Raw address must never be passed to a model."""

    status: str
    first_name: str
    preferred_language: str | None
    preferred_contact_channel: Literal["email", "web_chat", "none"]
    email: str | None


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=4000)
    pinned: bool = False


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    body: str
    author_id: uuid.UUID | None
    pinned: bool
    created_at: datetime


class TimelineEntryIn(BaseModel):
    customer_id: uuid.UUID
    occurred_at: datetime
    kind: Literal["message", "appointment", "reservation", "note", "campaign", "proposal", "order", "other"]
    ref_module: str
    ref_type: str
    ref_id: uuid.UUID
    summary: str = Field(max_length=200)


class TimelineEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    occurred_at: datetime
    kind: str
    ref_module: str
    ref_type: str
    ref_id: uuid.UUID
    summary: str


class ConsentStatus(BaseModel):
    channel: Channel
    purpose: Purpose
    status: ConsentStatusT | Literal["none"]


class EligibleContact(BaseModel):
    customer_id: uuid.UUID
    display_name: str
    contact_point_id: uuid.UUID
    value: str


class AudienceCandidate(BaseModel):
    """One customer of a marketing segment with everything needed to decide eligibility (never shown to the AI)."""

    customer_id: uuid.UUID
    display_name: str
    is_active: bool
    contact_point_id: uuid.UUID | None
    value: str | None
    consent_granted: bool


class UpdateCustomerPayload(BaseModel):
    """crm.update_customer proposal payload."""

    model_config = ConfigDict(extra="forbid")
    customer_id: uuid.UUID
    customer_version: int
    changes: CustomerFields

    @model_validator(mode="after")
    def _has_changes(self) -> UpdateCustomerPayload:
        if not self.changes.model_dump(exclude_unset=True):
            raise ValueError("no changes")
        return self
