from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionIn(Input):
    version: int = Field(ge=1)


class SupplierItemIn(Input):
    catalog_item_id: uuid.UUID
    supplier_sku: str | None = Field(default=None, max_length=100)
    last_price_minor: int = Field(ge=0, le=10**12, strict=True)
    lead_time_days: int = Field(default=0, ge=0, le=365)


class SupplierIn(Input):
    active: bool = True
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)
    items: list[SupplierItemIn] = Field(default_factory=list, max_length=200)

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name is required")
        return value.strip()


class SupplierUpdate(Input):
    active: bool | None = None
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)
    items: list[SupplierItemIn] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def nonnull_fields(self) -> SupplierUpdate:
        for key in ("name", "items", "active"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        if self.name is not None and not self.name.strip():
            raise ValueError("name is required")
        return self


class SupplierOut(SupplierIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: int
    created_at: datetime


class POLineIn(Input):
    catalog_item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit_cost_minor: int = Field(ge=0, le=10**12, strict=True)

    @field_validator("quantity", mode="before")
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("use a decimal string")
        return value


class POCreate(Input):
    supplier_id: uuid.UUID
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[POLineIn] = Field(min_length=1, max_length=200)


class POUpdate(VersionIn):
    expected_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[POLineIn] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def nonnull_lines(self) -> POUpdate:
        if "lines" in self.model_fields_set and self.lines is None:
            raise ValueError("lines cannot be null")
        return self


class PricedLine(POLineIn):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    name: str
    line_total_minor: int
    position: int


class POLineOut(PricedLine):
    id: uuid.UUID
    received_quantity: Decimal


class ReceiptLineIn(Input):
    po_line_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)

    @field_validator("quantity", mode="before")
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("use a decimal string")
        return value


class ReceiptIn(VersionIn):
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=200)
    override: bool = False
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def override_reason(self) -> ReceiptIn:
        if self.override and not (self.reason and self.reason.strip()):
            raise ValueError("override requires a reason")
        if len({line.po_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate receipt line")
        return self


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    received_by: uuid.UUID | None
    override_reason: str | None
    created_at: datetime
    lines: list[ReceiptLineIn] = Field(default_factory=list)


class POOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_name: str
    currency: str
    status: str
    number: str | None
    approved_date: date | None
    notes: str | None
    total_minor: int
    sent_at: datetime | None
    expected_delivery_date: date | None
    last_followup_at: datetime | None
    source_quote_id: uuid.UUID | None
    source_draft_id: uuid.UUID | None
    version: int
    created_at: datetime
    lines: list[POLineOut] = Field(default_factory=list)
    receipts: list[ReceiptOut] = Field(default_factory=list)


class PurchasingSuggestion(BaseModel):
    item_id: uuid.UUID
    item_name: str
    available: Decimal
    reorder_point: Decimal
    target_level: Decimal
    open_po_quantity: Decimal
    suggested: Decimal
    suppliers: list[SupplierOut]


class DraftConversion(VersionIn):
    draft_id: uuid.UUID
    supplier_id: uuid.UUID


class ConversionSnapshot(Input):
    supplier_name: str
    currency: str
    notes: str | None
    lines: list[PricedLine]
    total_minor: int


class RequestedDraftLine(Input):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=10000, max_digits=8, decimal_places=3)
    reason_text: str = Field(max_length=300)


class CreateFromDraftPayload(DraftConversion):
    requested_lines: list[RequestedDraftLine] | None = Field(default=None, min_length=1, max_length=100)
    snapshot: ConversionSnapshot | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.requested_lines is None:
            data.pop("requested_lines", None)  # preserve hashes of previously reviewed, unchanged payloads
        return data


class ApprovePOPayload(VersionIn):
    purchase_order_id: uuid.UUID
