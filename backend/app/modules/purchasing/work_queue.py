"""Pending supplier invitations for the cross-module Work queue."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.pagination import decode_cursor

from .models import QuoteRequest, QuoteRequestSupplier, Supplier


def awaiting_suppliers(
    db: Session, ctx: Context, *, limit: int = 50, cursor: str | None = None
) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    stmt = (
        select(QuoteRequestSupplier, QuoteRequest, Supplier.name)
        .join(
            QuoteRequest,
            (QuoteRequest.id == QuoteRequestSupplier.request_id)
            & (QuoteRequest.business_id == QuoteRequestSupplier.business_id),
        )
        .join(
            Supplier,
            (Supplier.id == QuoteRequestSupplier.supplier_id)
            & (Supplier.business_id == QuoteRequestSupplier.business_id),
        )
        .where(
            QuoteRequestSupplier.business_id == ctx.business_id,
            QuoteRequest.status == "sent",
            QuoteRequestSupplier.status == "invited",
        )
    )
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                QuoteRequestSupplier.created_at < ts,
                and_(QuoteRequestSupplier.created_at == ts, QuoteRequestSupplier.id < rid),
            )
        )
    return [
        {
            "id": invite.id,
            "kind": "supplier_quote",
            "title": f"Waiting for quote · {name}",
            "created_at": invite.created_at,
            "status": "awaiting_supplier",
            "due_date": request.reply_by,
            "quote_request_id": request.id,
            "supplier_id": invite.supplier_id,
        }
        for invite, request, name in db.execute(
            stmt.order_by(QuoteRequestSupplier.created_at.desc(), QuoteRequestSupplier.id.desc()).limit(limit)
        )
    ]
