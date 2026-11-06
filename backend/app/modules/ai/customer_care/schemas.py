from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal["checkin", "review_request", "recall", "complaint_reply"]


class MessageFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(default="", max_length=100)
    service_name: str = Field(default="", max_length=200)
    service_date: str = Field(default="", max_length=10)
    business_name: str = Field(max_length=200)
    review_url: str | None = Field(default=None, max_length=500)
    booking_url: str | None = Field(default=None, max_length=500)
    language: str = "en"
    tone: Literal["warm", "formal", "concise"] = "warm"


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=1000)

    @field_validator("subject", "body")
    @classmethod
    def plain(cls, value: str) -> str:
        if not value.strip() or any(c in value for c in ("\r", "\x00", "<", ">")):
            raise ValueError("plain text required")
        return value.strip()

    @field_validator("subject")
    @classmethod
    def single_line(cls, value: str) -> str:
        if "\n" in value:
            raise ValueError("subject must be a single line")
        return value


class SendMessage(Draft):
    customer_id: uuid.UUID
    kind: Kind
    appointment_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    service_date: date | None = None
    invoice_id: uuid.UUID | None = None
    source_message_id: uuid.UUID | None = None
    ai_generation: int | None = Field(default=None, ge=0)
    due_month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    review_requested: bool = False
    not_before: datetime

    @field_validator("not_before")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    @model_validator(mode="after")
    def references(self) -> SendMessage:
        if self.kind != "complaint_reply" and not all((self.appointment_id, self.service_id, self.service_date)):
            raise ValueError("appointment facts required")
        if self.kind == "complaint_reply" and self.source_message_id is None:
            raise ValueError("complaint source required")
        if self.kind == "recall" and self.due_month is None:
            raise ValueError("recall due month required")
        return self

    @property
    def domain_key(self) -> str:
        if self.kind == "recall":
            return f"recall:{self.customer_id}:{self.service_id}:{self.due_month}"
        subject = self.source_message_id if self.kind == "complaint_reply" else self.appointment_id
        return f"{self.kind}:{self.customer_id}:{subject}"
