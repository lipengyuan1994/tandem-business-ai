from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

Q = Numeric(14, 3)


def _fk(col: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


class StockLocation(BusinessScopedBase):
    __tablename__ = "stock_locations"
    name: Mapped[str] = mapped_column(String(120))
    location_id: Mapped[uuid.UUID | None]
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = scoped_table_args(
        Index("uq_stock_locations_default", "business_id", unique=True, postgresql_where=text("is_default"))
    )


class StockLevel(BusinessScopedBase):
    __tablename__ = "stock_levels"
    item_id: Mapped[uuid.UUID]
    stock_location_id: Mapped[uuid.UUID]
    on_hand: Mapped[Decimal] = mapped_column(Q, default=Decimal(0))
    reserved: Mapped[Decimal] = mapped_column(Q, default=Decimal(0))
    __table_args__ = scoped_table_args(
        _fk("item_id", "catalog_items"),
        _fk("stock_location_id", "stock_locations"),
        Index("uq_stock_levels", "business_id", "item_id", "stock_location_id", unique=True),
        CheckConstraint("on_hand >= 0", name="on_hand"),
        CheckConstraint("reserved >= 0", name="reserved"),
    )


class StockMovement(BusinessScopedBase):
    __tablename__ = "stock_movements"
    item_id: Mapped[uuid.UUID]
    stock_location_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(Q)
    reason: Mapped[str] = mapped_column(String(20))
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[uuid.UUID | None]
    note: Mapped[str | None] = mapped_column(Text)
    unit_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime]
    recorded_by: Mapped[uuid.UUID | None]
    proposal_id: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        _fk("item_id", "catalog_items"),
        _fk("stock_location_id", "stock_locations"),
        CheckConstraint("quantity <> 0", name="quantity"),
        CheckConstraint(
            "reason in ('receipt','consumption','sale','adjustment_gain','adjustment_loss','count_correction',"
            "'transfer_in','transfer_out','return')",
            name="reason",
        ),
        Index("ix_stock_movements_item", "business_id", "item_id", "occurred_at"),
    )


class StockReservation(BusinessScopedBase):
    __tablename__ = "stock_reservations"
    item_id: Mapped[uuid.UUID]
    stock_location_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(Q)
    status: Mapped[str] = mapped_column(String(10), default="active")
    ref_type: Mapped[str] = mapped_column(String(20))
    ref_id: Mapped[uuid.UUID]
    released_at: Mapped[datetime | None]
    consumed_movement_id: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        _fk("item_id", "catalog_items"),
        _fk("stock_location_id", "stock_locations"),
        CheckConstraint("quantity > 0", name="quantity"),
        CheckConstraint("status in ('active','released','consumed')", name="status"),
        CheckConstraint("ref_type in ('appointment','sales_order')", name="ref_type"),
        Index("ix_stock_reservations_ref", "business_id", "ref_type", "ref_id"),
    )


class ReorderRule(BusinessScopedBase):
    __tablename__ = "reorder_rules"
    item_id: Mapped[uuid.UUID]
    stock_location_id: Mapped[uuid.UUID]
    reorder_point: Mapped[Decimal] = mapped_column(Q)
    target_level: Mapped[Decimal] = mapped_column(Q)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)
    supplier_hint: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = scoped_table_args(
        _fk("item_id", "catalog_items"),
        _fk("stock_location_id", "stock_locations"),
        Index("uq_reorder_rules", "business_id", "item_id", "stock_location_id", unique=True),
        CheckConstraint("reorder_point >= 0 and target_level > reorder_point", name="levels"),
    )


class ReorderDraft(BusinessScopedBase):
    __tablename__ = "reorder_drafts"
    status: Mapped[str] = mapped_column(String(20), default="draft")
    note: Mapped[str | None] = mapped_column(Text)
    proposal_id: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        CheckConstraint("status in ('draft','sent_to_purchasing','cancelled')", name="status")
    )


class ReorderDraftLine(BusinessScopedBase):
    __tablename__ = "reorder_draft_lines"
    draft_id: Mapped[uuid.UUID]
    item_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(Q)
    reason_text: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = scoped_table_args(
        _fk("draft_id", "reorder_drafts"),
        _fk("item_id", "catalog_items"),
        CheckConstraint("quantity > 0", name="quantity"),
    )
