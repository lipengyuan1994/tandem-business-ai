from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["service", "product"]
ResourceKind = Literal["staff", "room", "equipment"]


class Money(BaseModel):
    amount_minor: int
    currency: str = Field(min_length=3, max_length=3)


class CatalogItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: Kind
    sku: str | None
    name: str
    description: str | None
    unit: str
    price_minor: int | None
    currency: str
    duration_minutes: int | None
    recall_interval_months: int | None = None
    buffer_minutes: int
    resource_kind: ResourceKind | None
    is_stock_tracked: bool
    is_bookable_online: bool
    status: Literal["active", "archived"]
    version: int


class _ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    unit: Literal["each", "hour", "kg", "l", "m"] = "each"
    price_minor: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    recall_interval_months: int | None = Field(default=None, ge=1, le=120)
    buffer_minutes: int = Field(default=0, ge=0, le=240)
    resource_kind: ResourceKind | None = None
    is_stock_tracked: bool = False
    is_bookable_online: bool = False


class CatalogItemCreate(_ItemFields):
    kind: Kind

    @model_validator(mode="after")
    def _kind_rules(self) -> CatalogItemCreate:
        if self.kind == "service":
            if self.duration_minutes is None or self.resource_kind is None:
                raise ValueError("catalog.service_requires_duration")
            if self.is_stock_tracked:
                raise ValueError("services are not stock tracked")
        elif self.recall_interval_months is not None:
            raise ValueError("products have no recall interval")
        elif self.duration_minutes is not None:
            raise ValueError("products have no duration")
        return self


class CatalogItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    sku: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price_minor: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=5, le=1440)
    recall_interval_months: int | None = Field(default=None, ge=1, le=120)
    buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    is_bookable_online: bool | None = None


class ServicePartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class ServicePartOut(BaseModel):
    item_id: uuid.UUID
    item_name: str
    quantity: Decimal


class ServiceRequirements(BaseModel):
    service_id: uuid.UUID
    name: str
    duration_minutes: int
    recall_interval_months: int | None = None
    buffer_minutes: int
    resource_kind: ResourceKind
    price_minor: int | None
    currency: str
    parts: list[ServicePartOut]


class PublicServiceOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    price: Money | None
