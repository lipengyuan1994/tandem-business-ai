from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


class CatalogItem(BusinessScopedBase):
    __tablename__ = "catalog_items"
    kind: Mapped[str] = mapped_column(String(10))
    sku: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(10), default="each")
    price_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    tax_code: Mapped[str | None] = mapped_column(String(32))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    recall_interval_months: Mapped[int | None] = mapped_column(Integer)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    required_resources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    resource_kind: Mapped[str | None] = mapped_column(String(12))
    is_stock_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bookable_online: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="active")
    __table_args__ = scoped_table_args(
        CheckConstraint("kind in ('service','product')", name="kind"),
        CheckConstraint("status in ('active','archived')", name="status"),
        CheckConstraint(
            "(kind = 'service' and duration_minutes between 5 and 1440"
            " and resource_kind is not null and not is_stock_tracked)"
            " or (kind = 'product' and duration_minutes is null)",
            name="kind_fields",
        ),
        CheckConstraint(
            "recall_interval_months is null or (kind = 'service' and recall_interval_months between 1 and 120)",
            name="recall_interval",
        ),
        Index("uq_catalog_items_sku", "business_id", "sku", unique=True, postgresql_where=text("sku is not null")),
    )


class ServicePart(BusinessScopedBase):
    __tablename__ = "service_parts"
    service_id: Mapped[uuid.UUID]
    item_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "service_id"], ["catalog_items.business_id", "catalog_items.id"]),
        ForeignKeyConstraint(["business_id", "item_id"], ["catalog_items.business_id", "catalog_items.id"]),
        Index("uq_service_parts", "service_id", "item_id", unique=True),
        CheckConstraint("quantity > 0", name="quantity"),
    )
