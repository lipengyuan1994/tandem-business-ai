"""Manual and future tool reads, with deterministic price, delivery and reliability calculations."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform import clock
from app.platform.context import Context

from . import permissions as P
from . import quote_repository as repo
from . import repository as po_repo
from .models import GoodsReceipt, PurchaseOrder, QuoteRequest, QuoteRequestLine, QuoteRequestSupplier, SupplierQuote
from .schemas_quotes import (
    IneligibleQuote,
    InviteOut,
    LatePO,
    OpenPOLine,
    QuoteComparison,
    QuoteRequestDue,
    QuoteRequestOut,
    QuoteScore,
    RequestLineOut,
    SupplierOption,
    SupplierQuoteOut,
    UnansweredInvite,
)


def today(db: Session, ctx: Context) -> date:
    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def request_lines(db: Session, ctx: Context, request_id: uuid.UUID) -> list[RequestLineOut]:
    rows = repo.lines(db, ctx, request_id)
    items = catalog.get_items(db, ctx, {r.item_id for r in rows})
    return [RequestLineOut(item_id=r.item_id, quantity=r.quantity, name=items[r.item_id].name) for r in rows]


def quote_out(db: Session, ctx: Context, row: SupplierQuote) -> SupplierQuoteOut:
    return SupplierQuoteOut.model_validate(
        {
            **{k: getattr(row, k) for k in SupplierQuoteOut.model_fields if k != "lines"},
            "lines": [
                dict(item_id=r.item_id, unit_price_minor=r.unit_price_minor, quantity_available=r.quantity_available)
                for r in repo.quote_lines(db, ctx, row.id)
            ],
        }
    )


def get_quote_request(db: Session, ctx: Context, quote_request_id: uuid.UUID) -> QuoteRequestOut:
    ctx.require(P.READ)
    row = repo.request(db, ctx, quote_request_id)
    return QuoteRequestOut.model_validate(
        {
            **{k: getattr(row, k) for k in QuoteRequestOut.model_fields if k not in {"lines", "suppliers", "quotes"}},
            "lines": request_lines(db, ctx, row.id),
            "suppliers": [
                InviteOut(
                    id=i.id,
                    supplier_id=i.supplier_id,
                    supplier_name=po_repo.supplier(db, ctx, i.supplier_id).name,
                    status=i.status,
                    sent_at=i.sent_at,
                    responded_at=i.responded_at,
                    last_followup_at=i.last_followup_at,
                )
                for i in repo.invites(db, ctx, row.id)
            ],
            "quotes": [quote_out(db, ctx, q) for q in repo.quotes(db, ctx, row.id)],
        }
    )


def find_open_quote_request(db: Session, ctx: Context, item_id: uuid.UUID) -> uuid.UUID | None:
    """Return the first sent request containing this tenant's item; drafts and closed requests do not block."""
    from app.platform.errors import NotFound

    ctx.require(P.READ)
    item_id = uuid.UUID(str(item_id))
    if item_id not in catalog.get_items(db, ctx, {item_id}):
        raise NotFound("purchasing.item_not_found")
    return db.scalar(
        select(QuoteRequest.id)
        .join(
            QuoteRequestLine,
            (QuoteRequestLine.request_id == QuoteRequest.id)
            & (QuoteRequestLine.business_id == QuoteRequest.business_id),
        )
        .where(
            QuoteRequest.business_id == ctx.business_id,
            QuoteRequest.status == "sent",
            QuoteRequestLine.business_id == ctx.business_id,
            QuoteRequestLine.item_id == item_id,
        )
        .order_by(QuoteRequest.created_at, QuoteRequest.id)
        .limit(1)
    )


def reliability(db: Session, ctx: Context, supplier_id: uuid.UUID) -> tuple[Fraction | None, int]:
    # Order by completion receipt, not order creation or last edit. Unknown promised dates count as not on time.
    completions = (
        select(GoodsReceipt.purchase_order_id, func.max(GoodsReceipt.created_at).label("received_at"))
        .where(GoodsReceipt.business_id == ctx.business_id)
        .group_by(GoodsReceipt.purchase_order_id)
        .subquery()
    )
    last = db.execute(
        select(completions.c.received_at, PurchaseOrder.id, PurchaseOrder.expected_delivery_date)
        .join(completions, completions.c.purchase_order_id == PurchaseOrder.id)
        .where(
            PurchaseOrder.business_id == ctx.business_id,
            PurchaseOrder.supplier_id == supplier_id,
            PurchaseOrder.status == "received",
        )
        .order_by(completions.c.received_at.desc(), PurchaseOrder.id.desc())
        .limit(10)
    ).all()
    if len(last) < 3:
        return None, len(last)
    zone = ZoneInfo(org.get_business_brief(db, ctx).timezone)
    on_time = sum(
        expected is not None and received.astimezone(zone).date() <= expected for received, _, expected in last
    )
    return Fraction(on_time, len(last)), len(last)


def list_suppliers_for_item(db: Session, ctx: Context, item_id: uuid.UUID) -> list[SupplierOption]:
    ctx.require(P.READ)
    # Validate the item within this tenant even if no suppliers exist.
    if item_id not in catalog.get_items(db, ctx, {item_id}):
        from app.platform.errors import NotFound

        raise NotFound("purchasing.item_not_found")
    result = []
    for supplier in po_repo.item_suppliers(db, ctx, item_id):
        item = next(i for i in po_repo.supplier_items(db, ctx, supplier.id) if i.catalog_item_id == item_id)
        rate, count = reliability(db, ctx, supplier.id)
        result.append(
            SupplierOption(
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                has_email=bool(supplier.email),
                last_price_minor=item.last_price_minor,
                currency=catalog.get_default_currency(db, ctx),
                lead_time_days=item.lead_time_days,
                on_time_rate=float(rate) if rate is not None else None,
                orders_count=count,
            )
        )
    return result


def total(db: Session, ctx: Context, quote: SupplierQuote) -> int:
    prices = {r.item_id: r.unit_price_minor for r in repo.quote_lines(db, ctx, quote.id)}
    return sum(
        int((r.quantity * prices.get(r.item_id, 0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        for r in repo.lines(db, ctx, quote.request_id)
    )


def compare_quotes(db: Session, ctx: Context, quote_request_id: uuid.UUID) -> QuoteComparison:
    ctx.require(P.READ)
    request = repo.request(db, ctx, quote_request_id)
    as_of = today(db, ctx)
    requested = {r.item_id: r.quantity for r in repo.lines(db, ctx, request.id)}
    currency = catalog.get_default_currency(db, ctx)
    eligible = []
    ineligible = []
    for q in repo.quotes(db, ctx, request.id):
        if q.status == "rejected":
            continue
        lines = {r.item_id: r for r in repo.quote_lines(db, ctx, q.id)}
        reasons = []
        if not requested.keys() <= lines.keys():
            reasons.append("incomplete")
        if q.currency != currency:
            reasons.append("other_currency")
        if q.valid_until is not None and q.valid_until < as_of:
            reasons.append("expired")
        if any(
            (q.min_quantity is not None and q.min_quantity > quantity)
            or (
                item in lines
                and lines[item].quantity_available is not None
                and (lines[item].quantity_available or Decimal(0)) < quantity
            )
            for item, quantity in requested.items()
        ):
            reasons.append("quantity")
        amount = total(db, ctx, q)
        if amount <= 0:
            reasons.append("invalid_total")
        if reasons:
            ineligible.append(
                IneligibleQuote.model_validate(dict(quote_id=q.id, supplier_id=q.supplier_id, reasons=reasons))
            )
        else:
            eligible.append((q, amount))
    cheapest = min((amount for _, amount in eligible), default=0)
    ranked = []
    for q, amount in eligible:
        price = Fraction(cheapest, amount)
        delivery = as_of + timedelta(days=q.lead_time_days)
        late = max(0, (delivery - request.needed_by).days)
        lead = max(Fraction(0), 1 - Fraction(late, 14))
        rate, count = reliability(db, ctx, q.supplier_id)
        reliable = rate if rate is not None else Fraction(4, 5)
        weighted = Fraction(3, 5) * price + Fraction(1, 4) * lead + Fraction(3, 20) * reliable
        # Nonnegative rational scores: round half up before converting any component to float.
        score = int(weighted * 1000 + Fraction(1, 2)) / 1000
        ranked.append(
            QuoteScore(
                quote_id=q.id,
                quote_version=q.version,
                supplier_id=q.supplier_id,
                supplier_name=po_repo.supplier(db, ctx, q.supplier_id).name,
                total_minor=amount,
                currency=q.currency,
                lead_time_days=q.lead_time_days,
                delivery_date=delivery,
                days_late=late,
                price_score=float(price),
                lead_score=float(lead),
                reliability=float(reliable),
                orders_count=count,
                score=float(score),
            )
        )
    ranked.sort(key=lambda q: (-q.score, q.total_minor, q.lead_time_days, str(q.quote_id)))
    return QuoteComparison(quote_request_id=request.id, as_of=as_of, ranked=ranked, ineligible=ineligible)


def list_late_purchase_orders(db: Session, ctx: Context, *, as_of: date | None = None) -> list[LatePO]:
    ctx.require(P.READ)
    day = as_of or today(db, ctx)
    result = []
    for po in db.scalars(
        select(PurchaseOrder)
        .where(
            PurchaseOrder.business_id == ctx.business_id,
            PurchaseOrder.status.in_(("sent", "partially_received")),
            PurchaseOrder.expected_delivery_date < day,
        )
        .order_by(PurchaseOrder.expected_delivery_date, PurchaseOrder.id)
    ):
        assert po.expected_delivery_date is not None and po.number is not None
        lines = [
            OpenPOLine(item_id=r.catalog_item_id, name=r.name, quantity=r.quantity - r.received_quantity)
            for r in po_repo.lines(db, ctx, po.id)
            if r.quantity > r.received_quantity
        ]
        if lines:
            result.append(
                LatePO(
                    id=po.id,
                    number=po.number,
                    supplier_id=po.supplier_id,
                    supplier_name=po.supplier_name,
                    expected_delivery_date=po.expected_delivery_date,
                    days_late=(day - po.expected_delivery_date).days,
                    last_followup_at=po.last_followup_at,
                    open_lines=lines,
                )
            )
    return result


def all_answered(db: Session, ctx: Context, request_id: uuid.UUID, *, as_of: date | None = None) -> bool:
    request = repo.request(db, ctx, request_id)
    invites = repo.invites(db, ctx, request_id)
    day = as_of or today(db, ctx)
    return bool(invites) and all(i.status != "invited" or day > request.reply_by + timedelta(days=7) for i in invites)


def list_quote_requests_due(db: Session, ctx: Context, *, as_of: date | None = None) -> list[QuoteRequestDue]:
    ctx.require(P.READ)
    day = as_of or today(db, ctx)
    result = []
    for r in db.scalars(
        select(QuoteRequest)
        .where(QuoteRequest.business_id == ctx.business_id, QuoteRequest.status == "sent")
        .order_by(QuoteRequest.reply_by, QuoteRequest.id)
    ):
        has_po = db.scalar(
            select(PurchaseOrder.id)
            .join(
                SupplierQuote,
                (PurchaseOrder.source_quote_id == SupplierQuote.id)
                & (PurchaseOrder.business_id == SupplierQuote.business_id),
            )
            .where(PurchaseOrder.business_id == ctx.business_id, SupplierQuote.request_id == r.id)
            .limit(1)
        )
        answered = all_answered(db, ctx, r.id, as_of=day)
        if has_po is None and (r.reply_by < day or answered):
            result.append(
                QuoteRequestDue(
                    quote_request_id=r.id, needed_by=r.needed_by, reply_by=r.reply_by, all_answered=answered
                )
            )
    return result


def list_unanswered_invites(db: Session, ctx: Context, *, as_of: date | None = None) -> list[UnansweredInvite]:
    ctx.require(P.READ)
    day = as_of or today(db, ctx)
    result = []
    rows = db.execute(
        select(QuoteRequestSupplier, QuoteRequest)
        .join(
            QuoteRequest,
            (QuoteRequest.id == QuoteRequestSupplier.request_id)
            & (QuoteRequest.business_id == QuoteRequestSupplier.business_id),
        )
        .where(
            QuoteRequest.business_id == ctx.business_id,
            QuoteRequest.status == "sent",
            QuoteRequestSupplier.status == "invited",
            QuoteRequest.reply_by < day,
        )
        .order_by(QuoteRequest.reply_by, QuoteRequestSupplier.id)
    )
    for invite, request in rows:
        if day > request.reply_by + timedelta(days=7):
            continue
        result.append(
            UnansweredInvite(
                id=invite.id,
                quote_request_id=request.id,
                supplier_id=invite.supplier_id,
                supplier_name=po_repo.supplier(db, ctx, invite.supplier_id).name,
                reply_by=request.reply_by,
                days_overdue=(day - request.reply_by).days,
                last_followup_at=invite.last_followup_at,
            )
        )
    return result
