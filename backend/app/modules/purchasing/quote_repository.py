"""Explicitly tenant-scoped quote reads. Writers lock the request first, then its invite or quote."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform import crud
from app.platform.context import Context
from app.platform.errors import NotFound

from .models import QuoteRequest, QuoteRequestLine, QuoteRequestSupplier, SupplierQuote, SupplierQuoteLine


def request(db: Session, ctx: Context, row_id: uuid.UUID, *, lock: bool = False) -> QuoteRequest:
    row = crud.require_scoped(db, ctx, QuoteRequest, row_id, "purchasing.quote_request_not_found", for_update=lock)
    if lock:
        db.refresh(row)
    return row


def lines(db: Session, ctx: Context, request_id: uuid.UUID) -> list[QuoteRequestLine]:
    return list(
        db.scalars(
            select(QuoteRequestLine)
            .where(QuoteRequestLine.business_id == ctx.business_id, QuoteRequestLine.request_id == request_id)
            .order_by(QuoteRequestLine.id)
        )
    )


def invites(db: Session, ctx: Context, request_id: uuid.UUID) -> list[QuoteRequestSupplier]:
    return list(
        db.scalars(
            select(QuoteRequestSupplier)
            .where(QuoteRequestSupplier.business_id == ctx.business_id, QuoteRequestSupplier.request_id == request_id)
            .order_by(QuoteRequestSupplier.id)
        )
    )


def invite(db: Session, ctx: Context, request_id: uuid.UUID, supplier_id: uuid.UUID) -> QuoteRequestSupplier:
    row = db.scalar(
        select(QuoteRequestSupplier)
        .where(
            QuoteRequestSupplier.business_id == ctx.business_id,
            QuoteRequestSupplier.request_id == request_id,
            QuoteRequestSupplier.supplier_id == supplier_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("purchasing.invite_not_found")
    return row


def quotes(db: Session, ctx: Context, request_id: uuid.UUID) -> list[SupplierQuote]:
    return list(
        db.scalars(
            select(SupplierQuote)
            .where(SupplierQuote.business_id == ctx.business_id, SupplierQuote.request_id == request_id)
            .order_by(SupplierQuote.id)
            .execution_options(populate_existing=True)
        )
    )


def quote(db: Session, ctx: Context, quote_id: uuid.UUID) -> SupplierQuote:
    return crud.require_scoped(db, ctx, SupplierQuote, quote_id, "purchasing.quote_not_found")


def quote_lines(db: Session, ctx: Context, quote_id: uuid.UUID) -> list[SupplierQuoteLine]:
    return list(
        db.scalars(
            select(SupplierQuoteLine)
            .where(SupplierQuoteLine.business_id == ctx.business_id, SupplierQuoteLine.quote_id == quote_id)
            .order_by(SupplierQuoteLine.id)
        )
    )
