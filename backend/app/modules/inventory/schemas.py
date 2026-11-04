from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Reason = Literal[
    "receipt",
    "consumption",
    "sale",
    "adjustment_gain",
    "adjustment_loss",
    "count_correction",
    "transfer_in",
    "transfer_out",
    "return",
]
Q = Field(max_digits=14, decimal_places=3)


class AvailabilityOut(BaseModel):
    item_id: uuid.UUID
    item_name: str
    sku: str | None
    unit: str
    stock_location_id: uuid.UUID
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    reorder_point: Decimal | None = None
    below_reorder: bool = False


class MovementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    quantity: Decimal = Field(max_digits=14, decimal_places=3)
    reason: Reason
    note: str | None = Field(default=None, max_length=1000)
    stock_location_id: uuid.UUID | None = None
    unit_cost_minor: int | None = Field(default=None, ge=0)
    ref_type: str | None = None
    ref_id: uuid.UUID | None = None


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    item_id: uuid.UUID
    stock_location_id: uuid.UUID
    quantity: Decimal
    reason: str
    ref_type: str | None
    ref_id: uuid.UUID | None
    note: str | None
    occurred_at: datetime
    recorded_by: uuid.UUID | None
    proposal_id: uuid.UUID | None


class CountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    counted: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    stock_location_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    item_id: uuid.UUID
    quantity: Decimal
    status: str
    ref_type: str
    ref_id: uuid.UUID
    created_at: datetime


class ReorderRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reorder_point: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    target_level: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    lead_time_days: int = Field(default=0, ge=0, le=365)
    supplier_hint: str | None = Field(default=None, max_length=200)


class ReorderRuleOut(ReorderRuleIn):
    model_config = ConfigDict(from_attributes=True)
    item_id: uuid.UUID
    stock_location_id: uuid.UUID


class ReorderSuggestion(BaseModel):
    item_id: uuid.UUID
    item_name: str
    available: Decimal
    reorder_point: Decimal
    target_level: Decimal
    suggested: Decimal
    explanation: str


class DraftLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    reason_text: str | None = Field(default=None, max_length=300)


class ReorderDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[DraftLineIn] = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class ReorderDraftOut(BaseModel):
    id: uuid.UUID
    status: str
    note: str | None
    created_at: datetime
    lines: list[DraftLineIn]


class ItemStockDetail(BaseModel):
    levels: list[AvailabilityOut]
    movements: list[MovementOut]
    reservations: list[ReservationOut]
    rule: ReorderRuleOut | None


class AdjustStockPayload(BaseModel):
    """inventory.adjust_stock proposal payload: a signed change with a reason (never an overwrite)."""

    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    quantity: Decimal = Field(max_digits=14, decimal_places=3)
    reason: Literal["adjustment_gain", "adjustment_loss", "count_correction", "receipt", "return"]
    note: str | None = Field(default=None, max_length=1000)
    stock_location_id: uuid.UUID | None = None


class ReorderDraftRead(ReorderDraftOut):
    version: int
