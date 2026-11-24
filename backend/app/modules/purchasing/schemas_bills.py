"""Supplier bills (accounts payable), bill payments and payables aging."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

BillStatus = Literal["open", "partially_paid", "paid", "void"]
BillPaymentMethod = Literal["cash", "card", "bank_transfer", "check", "other"]


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


Trimmed = Annotated[str | None, AfterValidator(_blank_to_none)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BillIn(_Input):
    """A bill from a supplier, optionally linked to one of its purchase orders (same supplier, approved or later)."""

    supplier_id: uuid.UUID
    purchase_order_id: uuid.UUID | None = None
    supplier_reference: Trimmed = Field(default=None, max_length=100)
    bill_date: date
    due_date: date | None = None
    total_minor: int = Field(gt=0, le=10**14, strict=True)
    notes: Trimmed = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def due_after_bill(self) -> BillIn:
        if self.due_date is not None and self.due_date < self.bill_date:
            raise ValueError("due_date cannot be before bill_date")
        return self


class BillUpdate(_Input):
    """Edits an open bill with no payments. Omitted fields stay as they are."""

    version: int = Field(ge=1)
    supplier_reference: Trimmed = Field(default=None, max_length=100)
    bill_date: date | None = None
    due_date: date | None = None
    total_minor: int | None = Field(default=None, gt=0, le=10**14, strict=True)
    notes: Trimmed = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def nonnull_fields(self) -> BillUpdate:
        for key in ("bill_date", "total_minor"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


class BillVoidIn(_Input):
    version: int = Field(ge=1)
    reason: Trimmed = Field(default=None, max_length=500)


class BillPaymentIn(_Input):
    """`version` is the bill version the staff member saw; the payment must fit the open balance."""

    version: int = Field(ge=1)
    amount_minor: int = Field(gt=0, le=10**14, strict=True)
    paid_on: date
    method: BillPaymentMethod
    reference: Trimmed = Field(default=None, max_length=200)


class BillPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount_minor: int
    paid_on: date
    method: BillPaymentMethod
    reference: str | None
    voided_at: datetime | None
    created_at: datetime


class BillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    purchase_order_id: uuid.UUID | None
    purchase_order_number: str | None = None
    supplier_reference: str | None
    bill_date: date
    due_date: date | None
    currency: str
    total_minor: int
    amount_paid_minor: int
    balance_minor: int = 0
    status: BillStatus
    notes: str | None
    voided_at: datetime | None
    version: int
    created_at: datetime
    payments: list[BillPaymentOut] = Field(default_factory=list)


class PurchaseOrderBilling(BaseModel):
    """How much of a purchase order has been received and billed, to prefill and check a new bill."""

    purchase_order_id: uuid.UUID
    currency: str
    ordered_minor: int
    received_minor: int
    billed_minor: int
    unbilled_received_minor: int


class PayablesBuckets(BaseModel):
    current_minor: int
    days_1_30_minor: int
    days_31_60_minor: int
    days_61_90_minor: int
    days_over_90_minor: int
    total_minor: int


class PayablesSupplier(PayablesBuckets):
    supplier_id: uuid.UUID
    supplier_name: str
    currency: str
    bill_count: int


class PayablesTotal(PayablesBuckets):
    currency: str
    bill_count: int


class PayablesAgingReport(BaseModel):
    """Open balances of supplier bills on `as_of`, bucketed by days past due (bill date when no due date)."""

    as_of: date
    suppliers: list[PayablesSupplier]
    totals: list[PayablesTotal]
