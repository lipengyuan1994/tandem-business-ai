"""Procurement inputs and read DTOs. Prices are integer minor units; quantities are exact decimals."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import Input, POLineIn

Quantity = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]


class RequestLine(Input):
    item_id: uuid.UUID
    quantity: Quantity

    @field_validator("quantity", mode="before")
    @classmethod
    def decimal_string(cls, value: object) -> object:
        return POLineIn.decimal_string(value)


class SendQuoteRequests(Input):
    lines: list[RequestLine] = Field(min_length=1, max_length=200)
    supplier_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    needed_by: date
    reply_by: date
    message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def unique_and_dates(self) -> SendQuoteRequests:
        if len(set(self.supplier_ids)) != len(self.supplier_ids) or len({x.item_id for x in self.lines}) != len(
            self.lines
        ):
            raise ValueError("duplicate supplier or item")
        if self.reply_by > self.needed_by:
            raise ValueError("reply_by must not follow needed_by")
        return self


class QuoteLine(Input):
    item_id: uuid.UUID
    unit_price_minor: int = Field(ge=0, le=10**12, strict=True)
    quantity_available: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)

    @field_validator("quantity_available", mode="before")
    @classmethod
    def decimal_string(cls, value: object) -> object:
        return POLineIn.decimal_string(value)


class QuoteInput(Input):
    lines: list[QuoteLine] = Field(min_length=1, max_length=200)
    lead_time_days: int = Field(ge=0, le=3650)
    min_quantity: Quantity | None = None
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("min_quantity", mode="before")
    @classmethod
    def decimal_string(cls, value: object) -> object:
        return POLineIn.decimal_string(value)

    @model_validator(mode="after")
    def unique_lines(self) -> QuoteInput:
        if len({x.item_id for x in self.lines}) != len(self.lines):
            raise ValueError("duplicate item")
        return self


class RecordSupplierQuote(QuoteInput):
    quote_request_id: uuid.UUID
    supplier_id: uuid.UUID
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source: Literal["form", "staff", "assistant_extracted"] = "staff"


class DeclineQuote(Input):
    declined: Literal[True]


class PublicQuoteInput(QuoteInput):
    declined: Literal[False] = False


class CreatePOFromQuote(Input):
    quote_id: uuid.UUID
    quote_version: int = Field(ge=1)


class QuoteAbout(Input):
    quote_request_id: uuid.UUID


class POAbout(Input):
    purchase_order_id: uuid.UUID


class SupplierFollowup(Input):
    supplier_id: uuid.UUID
    about: QuoteAbout | POAbout
    body: str = Field(min_length=1, max_length=2000)


class RequestLineOut(RequestLine):
    name: str


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    status: str
    sent_at: datetime | None
    responded_at: datetime | None
    last_followup_at: datetime | None


class SupplierQuoteOut(QuoteInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    request_id: uuid.UUID
    supplier_id: uuid.UUID
    currency: str
    source: str
    status: str
    version: int


class QuoteRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    needed_by: date
    reply_by: date
    message: str | None
    created_by: uuid.UUID | None
    version: int
    created_at: datetime
    lines: list[RequestLineOut]
    suppliers: list[InviteOut]
    quotes: list[SupplierQuoteOut]


class QuoteLinkOut(BaseModel):
    """A supplier's signed quote page. It works like a password for that supplier: never log, store or audit it."""

    url: str


class PublicQuoteRequest(BaseModel):
    business_name: str
    currency: str
    needed_by: date
    reply_by: date
    message: str | None
    lines: list[RequestLineOut]


class SupplierOption(BaseModel):
    supplier_id: uuid.UUID
    supplier_name: str
    has_email: bool
    last_price_minor: int
    currency: str
    lead_time_days: int
    on_time_rate: float | None
    orders_count: int


class QuoteScore(BaseModel):
    quote_id: uuid.UUID
    quote_version: int
    supplier_id: uuid.UUID
    supplier_name: str
    total_minor: int
    currency: str
    lead_time_days: int
    delivery_date: date
    days_late: int
    price_score: float
    lead_score: float
    reliability: float
    orders_count: int
    score: float


class IneligibleQuote(BaseModel):
    quote_id: uuid.UUID
    supplier_id: uuid.UUID
    reasons: list[Literal["incomplete", "other_currency", "expired", "quantity", "invalid_total"]]


class QuoteComparison(BaseModel):
    quote_request_id: uuid.UUID
    as_of: date
    ranked: list[QuoteScore]
    ineligible: list[IneligibleQuote]


class OpenPOLine(BaseModel):
    item_id: uuid.UUID
    name: str
    quantity: Decimal


class LatePO(BaseModel):
    id: uuid.UUID
    number: str
    supplier_id: uuid.UUID
    supplier_name: str
    expected_delivery_date: date
    days_late: int
    last_followup_at: datetime | None
    open_lines: list[OpenPOLine]


class QuoteRequestDue(BaseModel):
    quote_request_id: uuid.UUID
    needed_by: date
    reply_by: date
    all_answered: bool


class UnansweredInvite(BaseModel):
    id: uuid.UUID
    quote_request_id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    reply_by: date
    days_overdue: int
    last_followup_at: datetime | None


class StaffProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    current_revision_id: uuid.UUID | None
