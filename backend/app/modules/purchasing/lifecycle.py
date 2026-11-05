"""Business export and customer-identifier redaction in procurement free text."""

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSequence,
    QuoteRequest,
    QuoteRequestLine,
    QuoteRequestSupplier,
    Supplier,
    SupplierItem,
    SupplierQuote,
    SupplierQuoteLine,
)

TABLES = [
    Supplier,
    SupplierItem,
    PurchaseOrderSequence,
    PurchaseOrder,
    PurchaseOrderLine,
    GoodsReceipt,
    GoodsReceiptLine,
    QuoteRequest,
    QuoteRequestLine,
    QuoteRequestSupplier,
    SupplierQuote,
    SupplierQuoteLine,
]


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, TABLES)
    rows = tables["quote_request_suppliers"]

    def safe_rows() -> Iterator[dict[str, Any]]:
        for row in rows:
            row.pop("token_hash", None)
            yield row

    tables["quote_request_suppliers"] = safe_rows()
    return export_tables(tables, streaming=streaming)


def links(db: Session, ctx: Context, target: ErasureTarget) -> set[uuid.UUID]:
    """Identify customer-bearing requests/quotes/POs before any erasure handler changes their text."""
    ids: set[uuid.UUID] = set()
    for model, field in (
        (QuoteRequest, QuoteRequest.message),
        (SupplierQuote, SupplierQuote.notes),
        (PurchaseOrder, PurchaseOrder.notes),
    ):
        match = or_(model.id.in_(target.linked_ids), field.is_distinct_from(func.redact_text(field, target.needles)))
        if model is SupplierQuote:
            match = or_(match, SupplierQuote.request_id.in_(target.linked_ids))
        elif model is PurchaseOrder:
            match = or_(match, PurchaseOrder.source_quote_id.in_(target.linked_ids))
        for row in db.scalars(select(model).where(model.business_id == ctx.business_id, match)):
            ids.add(row.id)
            if isinstance(row, SupplierQuote):
                ids.add(row.request_id)
            elif isinstance(row, PurchaseOrder) and row.source_quote_id is not None:
                ids.add(row.source_quote_id)
    return ids


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    counts = {}
    for model, field, limit in (
        (QuoteRequest, QuoteRequest.message, 2000),
        (SupplierQuote, SupplierQuote.notes, 500),
        (PurchaseOrder, PurchaseOrder.notes, None),
    ):
        redacted = func.redact_text(field, target.needles)
        if limit is not None:
            redacted = func.left(redacted, limit)
        result = db.execute(
            update(model)
            .where(model.business_id == ctx.business_id, field.is_distinct_from(redacted))
            .values({field.key: redacted, "version": model.version + 1})
        )
        counts[model.__tablename__ + "_redacted"] = rows_affected(result)
    return counts
