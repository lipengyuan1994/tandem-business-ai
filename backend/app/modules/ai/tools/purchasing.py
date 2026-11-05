"""Purchasing tools (PQ-01 functions; contracts §10, integration-map §B/§D).

Read: list_suppliers_for_item, get_quote_request, compare_quotes, list_late_purchase_orders. Rankings and scores are
computed by purchasing. Propose (AI-07): propose_quote_request, propose_supplier_quote, propose_po_from_quote,
propose_supplier_followup. The tools check every id against purchasing's own reads and fill what the model must not
invent: quote-request quantities must equal the reorder suggestion, the quote version comes from the comparison, a
follow-up's supplier from the late order or the unanswered invite.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.modules.purchasing import public as purchasing
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Invalid, NotFound
from app.platform.money import money_text

from .base import RunState, Tool
from .common import NoArgs, ToolArgs, cite, day, listing, money, qty, ratio, redact_contacts, utc
from .proposing import PlainText, gather, propose, tool_result

READ = "purchasing.po.read"
PROPOSE = "ai.assistant.use"


class ItemArgs(ToolArgs):
    item_id: uuid.UUID


class QuoteRequestArgs(ToolArgs):
    quote_request_id: uuid.UUID


def _list_suppliers_for_item(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ItemArgs)
    rows = purchasing.list_suppliers_for_item(db, ctx, args.item_id)
    data = {
        "item_id": args.item_id,
        **listing(
            "suppliers",
            [
                {"supplier_id": s.supplier_id, "supplier_name": s.supplier_name, "has_email": s.has_email,
                 "last_price": money(s.last_price_minor, s.currency), "lead_time_days": s.lead_time_days,
                 "on_time_rate": ratio(s.on_time_rate), "orders_count": s.orders_count}
                for s in rows
            ],
        ),
    }  # fmt: skip
    cite(state, "list_suppliers_for_item", args.item_id, data)
    return data


def _get_quote_request(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, QuoteRequestArgs)
    qr = purchasing.get_quote_request(db, ctx, args.quote_request_id)
    data = {
        "quote_request_id": qr.id,
        "status": qr.status,
        "needed_by": day(qr.needed_by),
        "reply_by": day(qr.reply_by),
        **listing("lines", [{"item_id": x.item_id, "name": x.name, "quantity": qty(x.quantity)} for x in qr.lines]),
        "suppliers": [
            {"supplier_id": s.supplier_id, "supplier_name": s.supplier_name, "status": s.status,
             "responded_at": utc(s.responded_at), "last_followup_at": utc(s.last_followup_at)}
            for s in qr.suppliers
        ],
        "quotes": [
            {"quote_id": q.id, "quote_version": q.version, "supplier_id": q.supplier_id, "status": q.status,
             "lead_time_days": q.lead_time_days, "min_quantity": qty(q.min_quantity), "valid_until": day(q.valid_until),
             "notes": redact_contacts(q.notes, 300),
             "lines": [{"item_id": x.item_id, "unit_price": money(x.unit_price_minor, q.currency),
                        "quantity_available": qty(x.quantity_available)} for x in q.lines[:20]]}
            for q in qr.quotes
        ],
    }  # fmt: skip
    cite(state, "get_quote_request", qr.id, data)
    return data


def _compare_quotes(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, QuoteRequestArgs)
    c = purchasing.compare_quotes(db, ctx, args.quote_request_id)
    data = {
        "quote_request_id": c.quote_request_id,
        "as_of": day(c.as_of),
        "ranked": [
            {"rank": i, "quote_id": q.quote_id, "quote_version": q.quote_version, "supplier_id": q.supplier_id,
             "supplier_name": q.supplier_name, "total": money(q.total_minor, q.currency),
             "lead_time_days": q.lead_time_days, "delivery_date": day(q.delivery_date), "days_late": q.days_late,
             "score": ratio(q.score), "price_score": ratio(q.price_score), "lead_score": ratio(q.lead_score),
             "reliability": ratio(q.reliability), "orders_count": q.orders_count}
            for i, q in enumerate(c.ranked, start=1)
        ],
        "ineligible": [{"quote_id": q.quote_id, "supplier_id": q.supplier_id, "reasons": q.reasons}
                       for q in c.ineligible],
    }  # fmt: skip
    cite(state, "compare_quotes", c.quote_request_id, data)
    return data


def _list_late_purchase_orders(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    rows = purchasing.list_late_purchase_orders(db, ctx)
    data = listing(
        "purchase_orders",
        [
            {"purchase_order_id": p.id, "number": p.number, "supplier_id": p.supplier_id,
             "supplier_name": p.supplier_name, "expected_delivery_date": day(p.expected_delivery_date),
             "days_late": p.days_late, "last_followup_at": utc(p.last_followup_at),
             "open_lines": [{"item_id": x.item_id, "name": x.name, "quantity": qty(x.quantity)}
                            for x in p.open_lines[:20]]}
            for p in rows
        ],
    )  # fmt: skip
    cite(state, "list_late_purchase_orders", None, data)
    return data


# ----------------------------------------------------------------------------------------------- propose tools
class QuoteLineArgs(ToolArgs):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)


class ProposeQuoteRequestArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("lines",)
    lines: list[QuoteLineArgs] = Field(min_length=1, max_length=20)
    supplier_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    needed_by: date
    reply_by: date
    message: PlainText | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def consistent(self) -> ProposeQuoteRequestArgs:
        if len(set(self.supplier_ids)) != len(self.supplier_ids):
            raise ValueError("duplicate supplier")
        if len({x.item_id for x in self.lines}) != len(self.lines):
            raise ValueError("duplicate item")
        if self.reply_by > self.needed_by:
            raise ValueError("reply_by must not follow needed_by")
        return self


def _local_today(db: Session, ctx: Context) -> date:
    from zoneinfo import ZoneInfo

    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def _propose_quote_request(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeQuoteRequestArgs)
    if args.reply_by < _local_today(db, ctx):
        raise Invalid("ai.date_in_past", detail="reply_by is in the past")
    below = {s.item_id: s for s in inventory.list_below_reorder(db, ctx)}
    names: dict[uuid.UUID, str] = {}
    for line in args.lines:
        suggestion = below.get(line.item_id)
        if suggestion is None:
            raise Invalid("ai.not_below_reorder", detail=f"item {line.item_id} is not below its reorder point")
        if line.quantity != suggestion.suggested:
            raise Invalid("ai.quantity_not_suggested", detail=f"quantity must be {qty(suggestion.suggested)}")
        names[line.item_id] = suggestion.item_name
        offers = {o.supplier_id: o for o in purchasing.list_suppliers_for_item(db, ctx, line.item_id)}
        for supplier_id in args.supplier_ids:
            offer = offers.get(supplier_id)
            if offer is None or not offer.has_email:
                raise Invalid("ai.supplier_not_eligible", detail=f"supplier {supplier_id} cannot quote this item")
    payload: dict[str, Any] = {
        "lines": [{"item_id": str(x.item_id), "quantity": qty(x.quantity)} for x in args.lines],
        "supplier_ids": [str(s) for s in args.supplier_ids],
        "needed_by": args.needed_by.isoformat(),
        "reply_by": args.reply_by.isoformat(),
    }
    if args.message is not None:
        payload["message"] = args.message
    items = ", ".join(f"{names[x.item_id]} ×{qty(x.quantity)}" for x in args.lines)
    count = len(args.supplier_ids)
    title = f"Request quotes for {items} from {count} supplier{'s' if count > 1 else ''}"
    own = [tool_result("propose_quote_request", None, {"lines": payload["lines"], "suppliers": count,
                                                       "needed_by": payload["needed_by"],
                                                       "reply_by": payload["reply_by"]})]  # fmt: skip
    evidence = gather(db, ctx, state, own, [x.item_id for x in args.lines])
    return propose(db, ctx, action_type="purchasing.send_quote_requests", payload=payload, title=title,
                   evidence=evidence)  # fmt: skip


class SupplierQuoteLineArgs(ToolArgs):
    item_id: uuid.UUID
    unit_price_minor: int = Field(ge=0, le=10**12, strict=True)
    quantity_available: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)


class ProposeSupplierQuoteArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("quote_request_id", "supplier_id")
    quote_request_id: uuid.UUID
    supplier_id: uuid.UUID
    lines: list[SupplierQuoteLineArgs] = Field(min_length=1, max_length=20)
    lead_time_days: int = Field(ge=0, le=3650)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$", description="Default: the business currency.")
    min_quantity: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=3)
    valid_until: date | None = None
    notes: PlainText | None = Field(default=None, max_length=500)

    @field_validator("lines")
    @classmethod
    def unique_items(cls, value: list[SupplierQuoteLineArgs]) -> list[SupplierQuoteLineArgs]:
        if len({x.item_id for x in value}) != len(value):
            raise ValueError("duplicate item")
        return value


def _propose_supplier_quote(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeSupplierQuoteArgs)
    request = purchasing.get_quote_request(db, ctx, args.quote_request_id)
    invite = next((s for s in request.suppliers if s.supplier_id == args.supplier_id), None)
    if invite is None:
        raise Invalid("ai.supplier_not_invited", detail="the supplier was not invited to this quote request")
    requested = {x.item_id: x for x in request.lines}
    if any(x.item_id not in requested for x in args.lines):
        raise Invalid("ai.item_not_requested", detail="every line must be an item of the quote request")
    currency = args.currency or org.get_business_brief(db, ctx).currency
    payload: dict[str, Any] = {
        "quote_request_id": str(request.id), "supplier_id": str(args.supplier_id), "currency": currency,
        "lines": [{"item_id": str(x.item_id), "unit_price_minor": x.unit_price_minor,
                   **({"quantity_available": qty(x.quantity_available)} if x.quantity_available is not None else {})}
                  for x in args.lines],
        "lead_time_days": args.lead_time_days, "source": "assistant_extracted",
    }  # fmt: skip
    for field in ("min_quantity", "valid_until", "notes"):
        if (value := getattr(args, field)) is not None:
            payload[field] = qty(value) if isinstance(value, Decimal) else str(value)
    prices = ", ".join(f"{requested[x.item_id].name} {money_text(x.unit_price_minor, currency)}" for x in args.lines)
    title = f"Record quote from {invite.supplier_name}: {prices}, {args.lead_time_days} days"
    own = [tool_result("propose_supplier_quote", request.id, {"supplier": invite.supplier_name,
                                                             "lines": payload["lines"],
                                                             "lead_time_days": args.lead_time_days})]  # fmt: skip
    evidence = gather(db, ctx, state, own, [request.id, args.supplier_id])
    return propose(db, ctx, action_type="purchasing.record_supplier_quote", payload=payload, title=title,
                   evidence=evidence)  # fmt: skip


class ProposePoFromQuoteArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("quote_request_id",)
    quote_request_id: uuid.UUID
    quote_id: uuid.UUID


def _propose_po_from_quote(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposePoFromQuoteArgs)
    comparison = purchasing.compare_quotes(db, ctx, args.quote_request_id)
    ranked = {q.quote_id: (i, q) for i, q in enumerate(comparison.ranked, start=1)}
    if args.quote_id not in ranked:
        raise Invalid("ai.quote_not_eligible", detail="choose a quote_id from compare_quotes 'ranked'")
    rank, quote = ranked[args.quote_id]
    payload = {"quote_id": str(quote.quote_id), "quote_version": quote.quote_version}
    title = (
        f"Order from {quote.supplier_name}: {money_text(quote.total_minor, quote.currency)}, delivery "
        f"{quote.delivery_date:%a %d %b} (rank {rank} of {len(comparison.ranked)})"
    )
    facts = [{"rank": i, "supplier": q.supplier_name, "total": money_text(q.total_minor, q.currency),
              "delivery_date": day(q.delivery_date), "days_late": q.days_late, "score": ratio(q.score)}
             for i, q in enumerate(comparison.ranked, start=1)]  # fmt: skip
    own = [tool_result("compare_quotes", comparison.quote_request_id, facts)]
    evidence = gather(db, ctx, state, own, [comparison.quote_request_id, quote.quote_id])
    return propose(db, ctx, action_type="purchasing.create_po_from_quote", payload=payload, title=title,
                   evidence=evidence)  # fmt: skip


class ProposeSupplierFollowupArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("purchase_order_id", "quote_request_id", "supplier_id")
    purchase_order_id: uuid.UUID | None = Field(default=None, description="A late order (list_late_purchase_orders).")
    quote_request_id: uuid.UUID | None = Field(default=None, description="An unanswered quote request invite.")
    supplier_id: uuid.UUID | None = Field(default=None, description="With quote_request_id: the invited supplier.")
    body: PlainText = Field(max_length=2000)

    @model_validator(mode="after")
    def one_subject(self) -> ProposeSupplierFollowupArgs:
        if (self.purchase_order_id is None) == (self.quote_request_id is None):
            raise ValueError("give either purchase_order_id or quote_request_id")
        if self.quote_request_id is not None and self.supplier_id is None:
            raise ValueError("supplier_id is required with quote_request_id")
        return self


def _propose_supplier_followup(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeSupplierFollowupArgs)
    if args.purchase_order_id is not None:
        po = purchasing.get_late_purchase_order(db, ctx, args.purchase_order_id)
        if po is None:
            raise NotFound("ai.late_order_not_found", detail="not a late purchase order of this business")
        if args.supplier_id is not None and args.supplier_id != po.supplier_id:
            raise Invalid("ai.supplier_mismatch", detail="the order belongs to another supplier")
        supplier_id, supplier_name, ref = po.supplier_id, po.supplier_name, po.id
        about: dict[str, Any] = {"purchase_order_id": str(po.id)}
        subject = f"{po.number} ({po.days_late} days late)"
        facts: dict[str, Any] = {"number": po.number, "supplier": po.supplier_name, "days_late": po.days_late,
                                 "expected": day(po.expected_delivery_date)}  # fmt: skip
    else:
        assert args.quote_request_id is not None and args.supplier_id is not None
        invite = purchasing.get_unanswered_invite(db, ctx, args.quote_request_id, args.supplier_id)
        if invite is None:
            raise NotFound("ai.unanswered_invite_not_found", detail="no overdue, unanswered invite for that supplier")
        supplier_id, supplier_name, ref = invite.supplier_id, invite.supplier_name, invite.quote_request_id
        about = {"quote_request_id": str(invite.quote_request_id)}
        subject = f"a quote request (reply was due {invite.reply_by:%a %d %b})"
        facts = {"supplier": invite.supplier_name, "reply_by": day(invite.reply_by),
                 "days_overdue": invite.days_overdue}  # fmt: skip
    payload = {"supplier_id": str(supplier_id), "about": about, "body": args.body}
    title = f"Follow up with {supplier_name} about {subject}"
    own = [tool_result("propose_supplier_followup", ref, facts)]
    evidence = gather(db, ctx, state, own, [ref, supplier_id])
    return propose(db, ctx, action_type="purchasing.send_supplier_followup", payload=payload, title=title,
                   evidence=evidence)  # fmt: skip


TOOLS = (
    Tool(
        name="list_suppliers_for_item",
        description="Suppliers that sell an item: last price, lead time, on-time rate (0-1 or null), order count, "
        "whether they have an email address.",
        kind="read", permission=READ, args_model=ItemArgs, run=_list_suppliers_for_item,
    ),
    Tool(
        name="get_quote_request",
        description="A quote request: lines, needed-by and reply-by dates, invited suppliers with their status, "
        "and the quotes received.",
        kind="read", permission=READ, args_model=QuoteRequestArgs, run=_get_quote_request,
    ),
    Tool(
        name="compare_quotes",
        description="The received quotes of a request ranked by the business's deterministic score (price, lead "
        "time, reliability), with the score components and the quotes that are not eligible.",
        kind="read", permission=READ, args_model=QuoteRequestArgs, run=_compare_quotes,
    ),
    Tool(
        name="list_late_purchase_orders",
        description="Sent purchase orders past their expected delivery date, with days late and open lines.",
        kind="read", permission=READ, args_model=NoArgs, run=_list_late_purchase_orders,
    ),
    Tool(
        name="propose_quote_request",
        description="Propose asking up to 5 suppliers (from list_suppliers_for_item, with email) for quotes on items "
        "below their reorder point. Each quantity must equal the item's reorder suggestion.",
        kind="propose", permission=PROPOSE, args_model=ProposeQuoteRequestArgs, run=_propose_quote_request,
    ),
    Tool(
        name="propose_supplier_quote",
        description="Propose recording a supplier's quote for a quote request it was invited to (prices in minor "
        "units, lead time in days). A person confirms it.",
        kind="propose", permission=PROPOSE, args_model=ProposeSupplierQuoteArgs, run=_propose_supplier_quote,
    ),
    Tool(
        name="propose_po_from_quote",
        description="Propose a purchase order from one ranked quote of compare_quotes (the version is filled in).",
        kind="propose", permission=PROPOSE, args_model=ProposePoFromQuoteArgs, run=_propose_po_from_quote,
    ),
    Tool(
        name="propose_supplier_followup",
        description="Propose a plain-text follow-up to a supplier about a late purchase order or an unanswered "
        "quote request invite.",
        kind="propose", permission=PROPOSE, args_model=ProposeSupplierFollowupArgs, run=_propose_supplier_followup,
    ),
)  # fmt: skip
