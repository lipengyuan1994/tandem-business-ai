"""Non-AI approval adapters with exact integration-map payloads and revalidation at execution."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.platform.context import Context
from app.platform.errors import Invalid
from app.platform.money import money_text
from app.platform.quantities import format_quantity

from . import permissions as P
from . import quote_repository as repo
from . import quote_service as service
from . import repository as po_repo
from . import service as po_service
from .delivery import quote_money
from .previews import item_name, item_names, priced_line, total_line
from .schemas_quotes import (
    CreatePOFromQuote,
    POAbout,
    QuoteAbout,
    RecordSupplierQuote,
    SendQuoteRequests,
    SupplierFollowup,
)


def describe_send(db: Session, ctx: Context, p: SendQuoteRequests) -> list[PreviewLine]:
    names = item_names(db, ctx, {r.item_id for r in p.lines})
    return [
        PreviewLine("Suppliers", None, ", ".join(po_repo.supplier(db, ctx, s).name for s in p.supplier_ids)),
        PreviewLine("Needed by", None, str(p.needed_by)),
        PreviewLine("Reply by", None, str(p.reply_by)),
        *[PreviewLine(item_name(names, r.item_id), None, f"{format_quantity(r.quantity)} requested") for r in p.lines],
        PreviewLine("Message", None, p.message),
    ]


def run_send(db: Session, ctx: Context, p: SendQuoteRequests, meta: ExecutionMeta) -> dict[str, str]:
    return {"quote_request_id": str(service.send_requests(db, ctx, p, meta.idempotency_key).id)}


def validate_record(db: Session, ctx: Context, p: RecordSupplierQuote) -> None:
    if p.source == "form":
        raise Invalid("purchasing.invalid_quote_source")
    service.validate_record(db, ctx, p)


def describe_record(db: Session, ctx: Context, p: RecordSupplierQuote) -> list[PreviewLine]:
    names = item_names(db, ctx, {r.item_id for r in p.lines})
    return [
        PreviewLine("Supplier", None, po_repo.supplier(db, ctx, p.supplier_id).name),
        PreviewLine("Currency", None, p.currency),
        PreviewLine("Lead time (days)", None, str(p.lead_time_days)),
        *[
            PreviewLine(
                item_name(names, r.item_id),
                None,
                f"{money_text(r.unit_price_minor, p.currency)} each"
                + (f" · {format_quantity(r.quantity_available)} available" if r.quantity_available is not None else ""),
            )
            for r in p.lines
        ],
        PreviewLine("Minimum quantity", None, format_quantity(p.min_quantity) if p.min_quantity is not None else None),
        PreviewLine("Valid until", None, str(p.valid_until) if p.valid_until is not None else None),
        PreviewLine("Notes", None, p.notes),
        PreviewLine("Source", None, p.source),
    ]


def run_record(db: Session, ctx: Context, p: RecordSupplierQuote, meta: ExecutionMeta) -> dict[str, str]:
    validate_record(db, ctx, p)
    return {"quote_id": str(service.record_quote(db, ctx, p).id)}


def describe_po(db: Session, ctx: Context, p: CreatePOFromQuote) -> list[PreviewLine]:
    quote = repo.quote(db, ctx, p.quote_id)
    # The same pricing the execution applies (names, rounding, totals), so the preview cannot disagree with the PO.
    lines = po_service.prepare_lines(db, ctx, service.quote_po_data(db, ctx, quote).lines)
    return [
        PreviewLine("Supplier", None, po_repo.supplier(db, ctx, quote.supplier_id).name),
        PreviewLine("Quote version", None, str(p.quote_version)),
        PreviewLine("PO status", None, "draft"),
        *[
            priced_line(line.name, line.quantity, line.unit_cost_minor, line.line_total_minor, quote.currency)
            for line in lines
        ],
        total_line(sum(line.line_total_minor for line in lines), quote.currency),
        PreviewLine("Notes", None, quote.notes),
    ]


def run_po(db: Session, ctx: Context, p: CreatePOFromQuote, meta: ExecutionMeta) -> dict[str, str]:
    return {"purchase_order_id": str(service.po_from_quote(db, ctx, p))}


def about_text(db: Session, ctx: Context, about: QuoteAbout | POAbout) -> str:
    if isinstance(about, POAbout):
        order = po_repo.order(db, ctx, about.purchase_order_id)
        return f"Purchase order {order.number or 'draft'} · {order.supplier_name}"
    request = repo.request(db, ctx, about.quote_request_id)
    lines = repo.lines(db, ctx, request.id)
    names = item_names(db, ctx, {line.item_id for line in lines})
    items = ", ".join(f"{item_name(names, line.item_id)} × {format_quantity(line.quantity)}" for line in lines)
    return f"Quote request for {items} · reply by {request.reply_by}"


def describe_followup(db: Session, ctx: Context, p: SupplierFollowup) -> list[PreviewLine]:
    return [
        PreviewLine("Supplier", None, po_repo.supplier(db, ctx, p.supplier_id).name),
        PreviewLine("About", None, about_text(db, ctx, p.about)),
        PreviewLine("Message", None, p.body),
    ]


def run_followup(db: Session, ctx: Context, p: SupplierFollowup, meta: ExecutionMeta) -> dict[str, str]:
    return {"outbox_id": str(service.followup(db, ctx, p, meta.idempotency_key))}


SEND_QUOTE_REQUESTS = ExecutorSpec(
    "purchasing.send_quote_requests", SendQuoteRequests, P.APPROVE, describe_send, service.validate_send, run_send
)
RECORD_SUPPLIER_QUOTE = ExecutorSpec(
    "purchasing.record_supplier_quote", RecordSupplierQuote, P.WRITE, describe_record, validate_record, run_record
)
CREATE_PO_FROM_QUOTE = ExecutorSpec(
    "purchasing.create_po_from_quote",
    CreatePOFromQuote,
    P.APPROVE,
    describe_po,
    service.validate_po,
    run_po,
    chat_money=quote_money,
)
SEND_SUPPLIER_FOLLOWUP = ExecutorSpec(
    "purchasing.send_supplier_followup",
    SupplierFollowup,
    P.WRITE,
    describe_followup,
    service.validate_followup,
    run_followup,
)
ALL = (SEND_QUOTE_REQUESTS, RECORD_SUPPLIER_QUOTE, CREATE_PO_FROM_QUOTE, SEND_SUPPLIER_FOLLOWUP)
