"""Sales documents. Issued, paid and void tax documents retain bill-to snapshots on customer erasure."""

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


class DocumentSequence(BusinessScopedBase):
    __tablename__ = "document_sequences"
    doc_type: Mapped[str] = mapped_column(String(3))
    year: Mapped[int] = mapped_column(Integer)
    next_value: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "doc_type", "year"),
        CheckConstraint("doc_type in ('QUO','SO','INV','CN')", name="doc_type"),
        CheckConstraint("next_value > 0", name="next_value"),
    )


class Document(BusinessScopedBase):
    __abstract__ = True
    customer_id: Mapped[uuid.UUID]
    bill_to_name: Mapped[str] = mapped_column(String(200))
    bill_to_email: Mapped[str | None] = mapped_column(String(320))
    bill_to_address: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(12), default="draft")
    number: Mapped[str | None] = mapped_column(String(32))
    issue_date: Mapped[date | None]
    due_date: Mapped[date | None]
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0)


def document_args(statuses: str) -> tuple[object, ...]:
    return scoped_table_args(
        reference("customer_id", "customers"),
        UniqueConstraint("business_id", "number"),
        CheckConstraint(f"status in ({statuses})", name="status"),
        CheckConstraint(
            "subtotal_minor >= 0 and tax_minor >= 0 and total_minor = subtotal_minor + tax_minor", name="totals"
        ),
        CheckConstraint(
            "(status = 'draft' and number is null) or (status <> 'draft' and number is not null)", name="number"
        ),
    )


class Quote(Document):
    __tablename__ = "quotes"
    __table_args__ = document_args("'draft','sent','accepted','declined','expired'")


class SalesOrder(Document):
    __tablename__ = "sales_orders"
    source_quote_id: Mapped[uuid.UUID | None]
    __table_args__ = (
        *document_args("'draft','confirmed','fulfilled','cancelled'"),
        reference("source_quote_id", "quotes"),
        UniqueConstraint("business_id", "source_quote_id"),
    )


class Invoice(Document):
    __tablename__ = "invoices"
    kind: Mapped[str] = mapped_column(String(12), default="invoice")
    credit_of_invoice_id: Mapped[uuid.UUID | None]
    source_appointment_id: Mapped[uuid.UUID | None]
    source_order_id: Mapped[uuid.UUID | None]
    source_quote_id: Mapped[uuid.UUID | None]
    paid_at: Mapped[datetime | None]
    last_reminder_at: Mapped[datetime | None]
    __table_args__ = (
        *document_args("'draft','issued','paid','void'"),
        reference("credit_of_invoice_id", "invoices"),
        reference("source_appointment_id", "appointments"),
        reference("source_order_id", "sales_orders"),
        reference("source_quote_id", "quotes"),
        UniqueConstraint("business_id", "credit_of_invoice_id"),
        UniqueConstraint("business_id", "source_order_id"),
        UniqueConstraint("business_id", "source_appointment_id"),
        CheckConstraint(
            "(kind = 'invoice' and credit_of_invoice_id is null) or "
            "(kind = 'credit_note' and credit_of_invoice_id is not null and status = 'issued')",
            name="kind",
        ),
    )


class Line(BusinessScopedBase):
    __abstract__ = True
    document_id: Mapped[uuid.UUID]
    catalog_item_id: Mapped[uuid.UUID | None]
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    line_subtotal_minor: Mapped[int] = mapped_column(BigInteger)
    line_tax_minor: Mapped[int] = mapped_column(BigInteger)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer)


def line_args(table: str) -> tuple[object, ...]:
    return scoped_table_args(
        reference("document_id", table),
        reference("catalog_item_id", "catalog_items"),
        UniqueConstraint("business_id", "document_id", "position"),
        CheckConstraint("quantity > 0 and unit_price_minor >= 0 and tax_rate between 0 and 1", name="values"),
        CheckConstraint(
            "line_subtotal_minor >= 0 and line_tax_minor >= 0 "
            "and line_total_minor = line_subtotal_minor + line_tax_minor",
            name="totals",
        ),
    )


class QuoteLine(Line):
    __tablename__ = "quote_lines"
    __table_args__ = line_args("quotes")


class SalesOrderLine(Line):
    __tablename__ = "sales_order_lines"
    __table_args__ = line_args("sales_orders")


class InvoiceLine(Line):
    __tablename__ = "invoice_lines"
    __table_args__ = line_args("invoices")
