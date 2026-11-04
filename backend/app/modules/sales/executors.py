"""Approvals adapters to the same services as the staff API. No AI implementation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.crm import public as crm
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.money import money_text
from app.platform.quantities import format_quantity

from . import permissions as P
from . import repository as repo
from . import service
from . import service_creation as creation
from .calculations import prepare_lines, pricing_snapshot
from .models import Line
from .reminders import describe_reminder, run_reminder, validate_reminder
from .schemas import ConfirmOrderPayload, CreateQuotePayload, PaymentReminderPayload, PricedLine


def _priced_line(line: PricedLine | Line, currency: str) -> PreviewLine:
    """One line per item: "Brake service" -> "1 × 45.00 USD = 45.00 USD" (before tax)."""
    unit, subtotal = money_text(line.unit_price_minor, currency), money_text(line.line_subtotal_minor, currency)
    return PreviewLine(line.name, None, f"{format_quantity(line.quantity)} × {unit} = {subtotal}")


def _totals(currency: str, subtotal_minor: int, tax_minor: int, total_minor: int) -> list[PreviewLine]:
    return [
        PreviewLine("Subtotal", None, money_text(subtotal_minor, currency)),
        PreviewLine("Tax", None, money_text(tax_minor, currency)),
        PreviewLine("Total", None, money_text(total_minor, currency)),
    ]


def describe_create(db: Session, ctx: Context, p: CreateQuotePayload) -> list[PreviewLine]:
    customer = crm.get_customer_summary(db, ctx, p.customer_id)
    if p.pricing is None:
        raise Conflict("sales.price_changed", detail="Quote pricing must be captured before preview")
    pricing = p.pricing
    return [
        PreviewLine("Customer", None, customer.display_name),
        *[_priced_line(line, pricing.currency) for line in pricing.lines],
        *_totals(pricing.currency, pricing.subtotal_minor, pricing.tax_minor, pricing.total_minor),
    ]


def validate_create(db: Session, ctx: Context, p: CreateQuotePayload) -> None:
    crm.get_customer_summary(db, ctx, p.customer_id)
    currency = creation.business_currency(db, ctx)
    # Approvals invokes validate before serializing/hashing the immutable revision and rendering its preview.
    # Revisions always capture fresh pricing, replacing any client-supplied or older snapshot.
    p.pricing = pricing_snapshot(currency, prepare_lines(db, ctx, p.lines, currency))


def run_create(db: Session, ctx: Context, p: CreateQuotePayload, meta: ExecutionMeta) -> dict[str, Any]:
    if p.pricing is None:
        raise Conflict("sales.price_changed", detail="This proposal needs a new revision with captured quote pricing")
    return {"quote_id": str(creation.create(db, ctx, "quote", p, expected_pricing=p.pricing).id)}


def describe_confirm(db: Session, ctx: Context, p: ConfirmOrderPayload) -> list[PreviewLine]:
    row = repo.get(db, ctx, "order", p.order_id)
    return [
        PreviewLine("Customer", None, row.bill_to_name),
        PreviewLine("Order status", row.status, "confirmed"),
        *[_priced_line(line, row.currency) for line in repo.lines(db, ctx, "order", row.id)],
        *_totals(row.currency, row.subtotal_minor, row.tax_minor, row.total_minor),
    ]


def validate_confirm(db: Session, ctx: Context, p: ConfirmOrderPayload) -> None:
    row = repo.get(db, ctx, "order", p.order_id)
    service.check_version(row, p.version)
    service.require_status(row, "draft")


def run_confirm(db: Session, ctx: Context, p: ConfirmOrderPayload, meta: ExecutionMeta) -> dict[str, Any]:
    return {"order_id": str(service.transition(db, ctx, "order", p.order_id, p.version, "confirm").id)}


CREATE_QUOTE = ExecutorSpec(
    "sales.create_quote", CreateQuotePayload, P.PROPOSAL_APPROVE, describe_create, validate_create, run_create
)
CONFIRM_ORDER = ExecutorSpec(
    "sales.confirm_order", ConfirmOrderPayload, P.PROPOSAL_APPROVE, describe_confirm, validate_confirm, run_confirm
)
SEND_PAYMENT_REMINDER = ExecutorSpec(
    "sales.send_payment_reminder",
    PaymentReminderPayload,
    P.DOCUMENT_WRITE,
    describe_reminder,
    validate_reminder,
    run_reminder,
)
