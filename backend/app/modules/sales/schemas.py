from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
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

DocumentType = Literal["quote", "order", "invoice"]


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_item_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_price_minor: int | None = Field(default=None, ge=0, le=10**12, strict=True)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1, max_digits=6, decimal_places=4)

    @field_validator("quantity", "tax_rate", mode="before")
    @classmethod
    def decimal_input(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("use a decimal string instead of a floating-point number")
        return value

    @model_serializer(mode="wrap")
    def preserve_tax_omission(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.tax_rate is None:
            data.pop("tax_rate", None)
        return data

    @model_validator(mode="after")
    def free_text_fields(self) -> LineIn:
        if self.catalog_item_id is None and (self.name is None or self.unit_price_minor is None):
            raise ValueError("free-text lines require a name and unit_price_minor")
        if "tax_rate" in self.model_fields_set and self.tax_rate is None:
            raise ValueError("omit tax_rate to use the business default; an explicit rate cannot be null")
        return self


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: uuid.UUID
    bill_to_address: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class VersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class DocumentUpdate(VersionIn):
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[LineIn] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def nonnull_lines(self) -> DocumentUpdate:
        if "lines" in self.model_fields_set and self.lines is None:
            raise ValueError("lines cannot be null")
        return self


class PricedLine(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    catalog_item_id: uuid.UUID | None
    name: str
    quantity: Decimal
    unit_price_minor: int
    tax_rate: Decimal
    line_subtotal_minor: int
    line_tax_minor: int
    line_total_minor: int
    position: int


class LineOut(PricedLine):
    id: uuid.UUID


class QuotePricing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str
    lines: list[PricedLine]
    subtotal_minor: int
    tax_minor: int
    total_minor: int


class CreateQuotePayload(DocumentCreate):
    pricing: QuotePricing | None = None


PaymentMethod = Literal["cash", "card", "bank_transfer", "check", "other"]
PaymentStatus = Literal["unpaid", "partially_paid", "paid"]


class PaymentIn(VersionIn):
    """`version` is the invoice version the staff member saw; the payment must fit the open balance."""

    amount_minor: int = Field(gt=0, le=10**14, strict=True)
    paid_on: date
    method: PaymentMethod
    reference: str | None = Field(default=None, max_length=200)

    @field_validator("reference")
    @classmethod
    def blank_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount_minor: int
    paid_on: date
    method: PaymentMethod
    reference: str | None
    voided_at: datetime | None
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    customer_id: uuid.UUID
    bill_to_name: str
    bill_to_email: str | None
    bill_to_address: str | None
    currency: str
    status: str
    number: str | None
    issue_date: date | None
    due_date: date | None
    notes: str | None
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    kind: Literal["invoice", "credit_note"] | None = None
    credit_of_invoice_id: uuid.UUID | None = None
    source_appointment_id: uuid.UUID | None = None
    source_order_id: uuid.UUID | None = None
    source_quote_id: uuid.UUID | None = None
    paid_at: datetime | None = None
    last_reminder_at: datetime | None = None
    amount_paid_minor: int | None = None
    balance_minor: int | None = None
    payment_status: PaymentStatus | None = None
    version: int
    created_at: datetime
    lines: list[LineOut] = Field(default_factory=list)
    payments: list[PaymentOut] = Field(default_factory=list)


class ConfirmOrderPayload(VersionIn):
    order_id: uuid.UUID


class OverdueInvoice(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    bill_to_name: str
    bill_to_email: str | None
    due_date: date
    days_overdue: int
    total_minor: int
    balance_minor: int
    currency: str
    version: int
    last_reminder_at: datetime | None


class PaymentReminderIn(VersionIn):
    body: str = Field(min_length=1, max_length=2000)


class PaymentReminderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: uuid.UUID
    invoice_version: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=2000)


class ReminderProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    current_revision_id: uuid.UUID | None


class AgingBuckets(BaseModel):
    current_minor: int
    days_1_30_minor: int
    days_31_60_minor: int
    days_61_90_minor: int
    days_over_90_minor: int
    total_minor: int


class AgingCustomer(AgingBuckets):
    customer_id: uuid.UUID
    customer_name: str
    currency: str
    invoice_count: int


class AgingTotal(AgingBuckets):
    currency: str
    invoice_count: int


class AgingReport(BaseModel):
    """Open balances of issued invoices on `as_of`, bucketed by days past due (issue date when no due date)."""

    as_of: date
    customers: list[AgingCustomer]
    totals: list[AgingTotal]


class SalesAmounts(BaseModel):
    """Net of credit notes: invoices add, credit notes subtract. `net` excludes tax; `total` includes it."""

    currency: str
    invoice_count: int
    credit_note_count: int
    net_minor: int
    tax_minor: int
    total_minor: int


class SalesRow(SalesAmounts):
    """`key` is the period's first day, a customer id, a catalog item id, or `free_text` for typed-in lines."""

    key: str
    label: str
    quantity: Decimal | None = None


class SalesTotal(SalesAmounts):
    pass


class SalesReport(BaseModel):
    """Invoices count on their issue date (also when later credited); credit notes subtract on theirs."""

    date_from: date
    date_to: date
    group_by: Literal["period", "customer", "item"]
    period: Literal["day", "week", "month"]
    rows: list[SalesRow]
    totals: list[SalesTotal]


class MarginTotals(BaseModel):
    """Margin is over `costed_revenue_minor` only; revenue without a known cost is reported separately."""

    revenue_minor: int
    costed_revenue_minor: int
    uncosted_revenue_minor: int
    cost_minor: int | None
    margin_minor: int | None
    margin_basis_points: int | None = Field(description="Margin as a share of costed revenue; 2550 = 25.50%")


class MarginRow(MarginTotals):
    """`key` is a catalog item id or `free_text` (by item), or `product`, `service`, `free_text` (by kind)."""

    key: str
    label: str
    kind: Literal["product", "service", "free_text"] | None = None
    quantity: Decimal | None = None


class MarginReport(BaseModel):
    """Gross margin on net sales in the business currency at weighted average purchase cost as of `date_to`.
    Services cost their current parts (materials only). Documents in other currencies are left out and counted."""

    date_from: date
    date_to: date
    group_by: Literal["item", "kind"]
    currency: str
    cost_basis: Literal["average_purchase_cost"] = "average_purchase_cost"
    rows: list[MarginRow]
    totals: MarginTotals
    other_currency_document_count: int
