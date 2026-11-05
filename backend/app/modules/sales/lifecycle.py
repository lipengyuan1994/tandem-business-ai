"""Export all sales rows. Quotes, orders and draft invoices lose bill-to data and notes on erasure.

Issued, paid and void invoices and credit notes keep their bill-to snapshots for legal tax-document retention.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.platform import audit
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import DocumentSequence, Invoice, InvoiceLine, Quote, QuoteLine, SalesOrder, SalesOrderLine

TABLES = [DocumentSequence, Quote, QuoteLine, SalesOrder, SalesOrderLine, Invoice, InvoiceLine]


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, TABLES)
    return export_tables(tables, streaming=streaming)


def links(db: Session, ctx: Context, target: ErasureTarget) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for model in (Quote, SalesOrder, Invoice):
        ids.update(
            db.scalars(
                select(model.id).where(
                    model.business_id == ctx.business_id,
                    model.customer_id == target.customer_id,
                )
            )
        )
    return ids


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model in (Quote, SalesOrder, Invoice):
        stmt = update(model).where(model.business_id == ctx.business_id, model.customer_id == target.customer_id)
        if model is Invoice:
            stmt = stmt.where(Invoice.status == "draft")
        counts[model.__tablename__] = rows_affected(
            db.execute(
                stmt.values(
                    bill_to_name="[erased]",
                    bill_to_email="[erased]",
                    bill_to_address="[erased]",
                    notes=None,
                    version=model.version + 1,
                )
            )
        )
    audit.record(
        db,
        ctx,
        action="sales.customer_erased",
        target_type="customer",
        target_id=target.customer_id,
        summary="Erased sales drafts, quotes and orders; retained issued tax-document snapshots",
        changes=counts,
    )
    return counts
