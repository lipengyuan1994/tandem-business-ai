"""Public purchasing reads; inventory does not depend on purchasing."""

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import permissions as P
from . import quote_reads, repository
from .events import QuoteReceived
from .models import PurchaseOrder, QuoteRequest, QuoteRequestSupplier
from .quote_reads import (
    compare_quotes,
    find_open_quote_request,
    get_quote_request,
    list_late_purchase_orders,
    list_quote_requests_due,
    list_suppliers_for_item,
    list_unanswered_invites,
)
from .repository import open_quantities
from .schemas_quotes import (
    LatePO,
    OpenPOLine,
    QuoteComparison,
    QuoteRequestDue,
    QuoteRequestOut,
    SupplierOption,
    UnansweredInvite,
)

__all__ = [
    "LatePO",
    "QuoteComparison",
    "QuoteReceived",
    "QuoteRequestDue",
    "QuoteRequestOut",
    "SupplierOption",
    "UnansweredInvite",
    "compare_quotes",
    "find_open_quote_request",
    "get_late_purchase_order",
    "get_quote_request",
    "get_unanswered_invite",
    "list_late_purchase_orders",
    "list_quote_requests_due",
    "list_suppliers_for_item",
    "list_unanswered_invites",
    "open_quantities",
]


def get_late_purchase_order(
    db: Session, ctx: Context, purchase_order_id: uuid.UUID, *, as_of: date | None = None
) -> LatePO | None:
    """One currently late, open order of this business; absent/ineligible targets return None."""
    ctx.require(P.READ)
    day = as_of or quote_reads.today(db, ctx)
    po = db.scalar(
        select(PurchaseOrder).where(
            PurchaseOrder.business_id == ctx.business_id,
            PurchaseOrder.id == purchase_order_id,
            PurchaseOrder.status.in_(("sent", "partially_received")),
            PurchaseOrder.expected_delivery_date < day,
        )
    )
    if po is None:
        return None
    lines = [
        OpenPOLine(item_id=r.catalog_item_id, name=r.name, quantity=r.quantity - r.received_quantity)
        for r in repository.lines(db, ctx, po.id)
        if r.quantity > r.received_quantity
    ]
    if not lines:
        return None
    assert po.number is not None and po.expected_delivery_date is not None
    return LatePO(
        id=po.id,
        number=po.number,
        supplier_id=po.supplier_id,
        supplier_name=po.supplier_name,
        expected_delivery_date=po.expected_delivery_date,
        days_late=(day - po.expected_delivery_date).days,
        last_followup_at=po.last_followup_at,
        open_lines=lines,
    )


def get_unanswered_invite(
    db: Session, ctx: Context, quote_request_id: uuid.UUID, supplier_id: uuid.UUID, *, as_of: date | None = None
) -> UnansweredInvite | None:
    """One overdue, live invitation, scoped to business, request and supplier."""
    ctx.require(P.READ)
    day = as_of or quote_reads.today(db, ctx)
    row = db.execute(
        select(QuoteRequestSupplier, QuoteRequest)
        .join(
            QuoteRequest,
            (QuoteRequest.id == QuoteRequestSupplier.request_id)
            & (QuoteRequest.business_id == QuoteRequestSupplier.business_id),
        )
        .where(
            QuoteRequest.business_id == ctx.business_id,
            QuoteRequest.id == quote_request_id,
            QuoteRequestSupplier.supplier_id == supplier_id,
            QuoteRequest.status == "sent",
            QuoteRequestSupplier.status == "invited",
            QuoteRequest.reply_by < day,
            QuoteRequest.reply_by >= day - timedelta(days=7),
        )
    ).first()
    if row is None:
        return None
    invite, request = row
    return UnansweredInvite(
        id=invite.id,
        quote_request_id=request.id,
        supplier_id=invite.supplier_id,
        supplier_name=repository.supplier(db, ctx, invite.supplier_id).name,
        reply_by=request.reply_by,
        days_overdue=(day - request.reply_by).days,
        last_followup_at=invite.last_followup_at,
    )
