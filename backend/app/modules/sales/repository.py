"""Business-scoped persistence, transactional numbering and document locks."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.platform import crud
from app.platform.context import Context
from app.platform.ids import new_id

from .models import Document, DocumentSequence, Invoice, InvoiceLine, Line, Quote, QuoteLine, SalesOrder, SalesOrderLine
from .schemas import DocumentType

MODELS: dict[DocumentType, type[Document]] = {"quote": Quote, "order": SalesOrder, "invoice": Invoice}
LINES: dict[DocumentType, type[Line]] = {"quote": QuoteLine, "order": SalesOrderLine, "invoice": InvoiceLine}


def get(db: Session, ctx: Context, family: DocumentType, row_id: uuid.UUID, *, lock: bool = False) -> Document:
    return crud.require_scoped(db, ctx, MODELS[family], row_id, "sales.document_not_found", for_update=lock)


def lines(db: Session, ctx: Context, family: DocumentType, row_id: uuid.UUID) -> list[Line]:
    model = LINES[family]
    return list(
        db.scalars(
            select(model)
            .where(
                model.business_id == ctx.business_id,
                model.document_id == row_id,
            )
            .order_by(model.position)
        )
    )


def delete_lines(db: Session, ctx: Context, family: DocumentType, row_id: uuid.UUID) -> None:
    model = LINES[family]
    db.execute(delete(model).where(model.business_id == ctx.business_id, model.document_id == row_id))


def list_documents(
    db: Session,
    ctx: Context,
    family: DocumentType,
    *,
    limit: int,
    cursor: str | None,
    status: str | None = None,
    customer_id: uuid.UUID | None = None,
    overdue_as_of: date | None = None,
) -> tuple[list[Document], str | None]:
    model = MODELS[family]
    stmt = select(model).where(model.business_id == ctx.business_id)
    if status:
        stmt = stmt.where(model.status == status)
    if customer_id:
        stmt = stmt.where(model.customer_id == customer_id)
    if overdue_as_of is not None:
        stmt = stmt.where(Invoice.kind == "invoice", model.status == "issued", model.due_date < overdue_as_of)
    return crud.page(db, stmt, model, limit, cursor)


def next_number(db: Session, ctx: Context, prefix: str, year: int) -> str:
    db.execute(
        insert(DocumentSequence)
        .values(
            id=new_id(),
            business_id=ctx.business_id,
            doc_type=prefix,
            year=year,
            next_value=1,
        )
        .on_conflict_do_nothing(index_elements=["business_id", "doc_type", "year"])
    )
    sequence = db.scalar(
        select(DocumentSequence)
        .where(
            DocumentSequence.business_id == ctx.business_id,
            DocumentSequence.doc_type == prefix,
            DocumentSequence.year == year,
        )
        .with_for_update()
    )
    assert sequence is not None
    value = sequence.next_value
    sequence.next_value += 1
    sequence.version += 1
    db.flush()
    return f"{prefix}-{year}-{value:04d}"


def converted(db: Session, ctx: Context, family: DocumentType, source: str, row_id: uuid.UUID) -> bool:
    model = MODELS[family]
    return (
        db.scalar(
            select(model.id).where(
                model.business_id == ctx.business_id,
                getattr(model, source) == row_id,
            )
        )
        is not None
    )


def export_invoices(db: Session, ctx: Context, start: date, end: date) -> list[Invoice]:
    return list(
        db.scalars(
            select(Invoice)
            .where(
                Invoice.business_id == ctx.business_id,
                Invoice.status.in_(("issued", "paid", "void")),
                Invoice.issue_date >= start,
                Invoice.issue_date <= end,
            )
            .order_by(Invoice.issue_date, Invoice.number)
        )
    )
