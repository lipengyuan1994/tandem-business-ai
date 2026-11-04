"""Supplier master data, purchase documents and immutable receipt ledger."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


def reference(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", column], [f"{table}.business_id", f"{table}.id"])


class Supplier(BusinessScopedBase):
    __tablename__ = "suppliers"
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = scoped_table_args()


class SupplierItem(BusinessScopedBase):
    __tablename__ = "supplier_items"
    supplier_id: Mapped[uuid.UUID]
    catalog_item_id: Mapped[uuid.UUID]
    supplier_sku: Mapped[str | None] = mapped_column(String(100))
    last_price_minor: Mapped[int] = mapped_column(BigInteger)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = scoped_table_args(
        reference("supplier_id", "suppliers"),
        reference("catalog_item_id", "catalog_items"),
        UniqueConstraint("business_id", "supplier_id", "catalog_item_id"),
        CheckConstraint("last_price_minor >= 0 and lead_time_days >= 0", name="values"),
    )


class PurchaseOrderSequence(BusinessScopedBase):
    __tablename__ = "purchase_order_sequences"
    year: Mapped[int] = mapped_column(Integer)
    next_value: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "year"),
        CheckConstraint("next_value > 0", name="next_value"),
    )


class PurchaseOrder(BusinessScopedBase):
    __tablename__ = "purchase_orders"
    supplier_id: Mapped[uuid.UUID]
    supplier_name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    number: Mapped[str | None] = mapped_column(String(32))
    approved_date: Mapped[date | None]
    notes: Mapped[str | None] = mapped_column(Text)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    sent_at: Mapped[datetime | None]
    expected_delivery_date: Mapped[date | None]
    last_followup_at: Mapped[datetime | None]
    source_quote_id: Mapped[uuid.UUID | None]
    source_draft_id: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        reference("supplier_id", "suppliers"),
        reference("source_draft_id", "reorder_drafts"),
        reference("source_quote_id", "supplier_quotes"),
        UniqueConstraint("business_id", "source_quote_id"),
        UniqueConstraint("business_id", "number"),
        UniqueConstraint("business_id", "source_draft_id"),
        CheckConstraint(
            "status in ('draft','approved','sent','partially_received','received','cancelled')", name="status"
        ),
        CheckConstraint("total_minor >= 0", name="total"),
        CheckConstraint(
            "(status in ('draft','cancelled')) or (number is not null and approved_date is not null)", name="number"
        ),
    )


class PurchaseOrderLine(BusinessScopedBase):
    __tablename__ = "purchase_order_lines"
    purchase_order_id: Mapped[uuid.UUID]
    catalog_item_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal(0))
    unit_cost_minor: Mapped[int] = mapped_column(BigInteger)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = scoped_table_args(
        reference("purchase_order_id", "purchase_orders"),
        reference("catalog_item_id", "catalog_items"),
        UniqueConstraint("business_id", "purchase_order_id", "position"),
        CheckConstraint(
            "quantity > 0 and received_quantity >= 0 and unit_cost_minor >= 0 and line_total_minor >= 0", name="values"
        ),
    )


class GoodsReceipt(BusinessScopedBase):
    __tablename__ = "goods_receipts"
    purchase_order_id: Mapped[uuid.UUID]
    received_by: Mapped[uuid.UUID | None]
    override_reason: Mapped[str | None] = mapped_column(String(1000))
    __table_args__ = scoped_table_args(reference("purchase_order_id", "purchase_orders"))


class GoodsReceiptLine(BusinessScopedBase):
    __tablename__ = "goods_receipt_lines"
    receipt_id: Mapped[uuid.UUID]
    po_line_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    __table_args__ = scoped_table_args(
        reference("receipt_id", "goods_receipts"),
        reference("po_line_id", "purchase_order_lines"),
        UniqueConstraint("business_id", "receipt_id", "po_line_id"),
        CheckConstraint("quantity > 0", name="quantity"),
    )


class QuoteRequest(BusinessScopedBase):
    __tablename__ = "quote_requests"
    status: Mapped[str] = mapped_column(String(16), default="draft")
    needed_by: Mapped[date]
    reply_by: Mapped[date]
    message: Mapped[str | None] = mapped_column(String(2000))
    created_by: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        CheckConstraint("status in ('draft','sent','closed','cancelled')", name="status"),
        CheckConstraint("reply_by <= needed_by", name="dates"),
    )


class QuoteRequestLine(BusinessScopedBase):
    __tablename__ = "quote_request_lines"
    request_id: Mapped[uuid.UUID]
    item_id: Mapped[uuid.UUID]
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    __table_args__ = scoped_table_args(
        reference("request_id", "quote_requests"),
        reference("item_id", "catalog_items"),
        UniqueConstraint("business_id", "request_id", "item_id"),
        CheckConstraint("quantity > 0", name="quantity"),
    )


class QuoteRequestSupplier(BusinessScopedBase):
    __tablename__ = "quote_request_suppliers"
    request_id: Mapped[uuid.UUID]
    supplier_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="invited")
    sent_at: Mapped[datetime | None]
    responded_at: Mapped[datetime | None]
    last_followup_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        reference("request_id", "quote_requests"),
        reference("supplier_id", "suppliers"),
        UniqueConstraint("business_id", "request_id", "supplier_id"),
        CheckConstraint("status in ('invited','responded','declined','expired')", name="status"),
    )


class SupplierQuote(BusinessScopedBase):
    __tablename__ = "supplier_quotes"
    request_id: Mapped[uuid.UUID]
    supplier_id: Mapped[uuid.UUID]
    currency: Mapped[str] = mapped_column(String(3))
    lead_time_days: Mapped[int] = mapped_column(Integer)
    min_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    valid_until: Mapped[date | None]
    notes: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="received")
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(
            ["business_id", "request_id", "supplier_id"],
            [
                "quote_request_suppliers.business_id",
                "quote_request_suppliers.request_id",
                "quote_request_suppliers.supplier_id",
            ],
        ),
        UniqueConstraint("business_id", "request_id", "supplier_id"),
        CheckConstraint("source in ('form','staff','assistant_extracted')", name="source"),
        CheckConstraint("status in ('received','accepted','rejected')", name="status"),
        CheckConstraint("lead_time_days >= 0 and (min_quantity is null or min_quantity > 0)", name="values"),
    )


class SupplierQuoteLine(BusinessScopedBase):
    __tablename__ = "supplier_quote_lines"
    quote_id: Mapped[uuid.UUID]
    item_id: Mapped[uuid.UUID]
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    quantity_available: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    __table_args__ = scoped_table_args(
        reference("quote_id", "supplier_quotes"),
        reference("item_id", "catalog_items"),
        UniqueConstraint("business_id", "quote_id", "item_id"),
        CheckConstraint(
            "unit_price_minor >= 0 and (quantity_available is null or quantity_available >= 0)", name="values"
        ),
    )
