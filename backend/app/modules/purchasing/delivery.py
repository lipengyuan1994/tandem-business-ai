"""PO delivery through the existing transition and transactional notification API."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid
from app.platform.money import money_text
from app.platform.quantities import format_quantity
from app.platform.registry import ChatMoney

from . import permissions as P
from . import repository as repo
from . import service
from .models import PurchaseOrder
from .previews import priced_line, total_line
from .schemas import ApprovePOPayload, CreateFromDraftPayload
from .schemas_quotes import CreatePOFromQuote


def validate_send(db: Session, ctx: Context, p: ApprovePOPayload) -> None:
    row = repo.order(db, ctx, p.purchase_order_id, lock=True)
    if row.sent_at is not None:
        raise Conflict("purchasing.po_already_sent")
    service.check_version(row, p.version)
    service.check_transition(row, "send")
    supplier = repo.supplier(db, ctx, row.supplier_id, lock=True)
    if not supplier.active:
        raise Invalid("purchasing.supplier_inactive")
    if not supplier.email:
        raise Invalid("purchasing.supplier_email_required")


def second_person(db: Session, ctx: Context, p: ApprovePOPayload) -> bool:
    row = repo.order(db, ctx, p.purchase_order_id, lock=True)
    return (
        row.total_minor > 50000
        or row.currency != org.get_business_brief(db, ctx).currency
        or db.scalar(
            select(PurchaseOrder.id)
            .where(
                PurchaseOrder.business_id == ctx.business_id,
                PurchaseOrder.supplier_id == row.supplier_id,
                PurchaseOrder.id != row.id,
                PurchaseOrder.sent_at.is_not(None),
            )
            .limit(1)
        )
        is None
    )


def po_money(db: Session, ctx: Context, p: ApprovePOPayload) -> ChatMoney:
    row = repo.order(db, ctx, p.purchase_order_id, lock=True)
    subject = (
        f"draft:{row.source_draft_id}"
        if row.source_draft_id
        else f"quote:{row.source_quote_id}"
        if row.source_quote_id
        else f"po:{row.id}"
    )
    return ChatMoney(row.total_minor, row.currency, subject)


def draft_money(db: Session, ctx: Context, p: CreateFromDraftPayload) -> ChatMoney:
    from .executors import snapshot

    current = snapshot(db, ctx, p, lock=True)
    if p.snapshot is None or current != p.snapshot:
        raise Conflict("purchasing.preview_changed")
    return ChatMoney(current.total_minor, current.currency, f"draft:{p.draft_id}")


def quote_money(db: Session, ctx: Context, p: CreatePOFromQuote) -> ChatMoney:
    from . import quote_repository, quote_service

    row = quote_repository.quote(db, ctx, p.quote_id)
    db.refresh(row, with_for_update=True)
    data = quote_service.quote_po_data(db, ctx, row)
    lines = service.prepare_lines(db, ctx, data.lines)
    return ChatMoney(sum(line.line_total_minor for line in lines), row.currency, f"quote:{row.id}")


def describe_send(db: Session, ctx: Context, p: ApprovePOPayload) -> list[PreviewLine]:
    row = repo.order(db, ctx, p.purchase_order_id)
    return [
        PreviewLine("Purchase order", None, row.number),
        PreviewLine("Supplier", None, row.supplier_name),
        PreviewLine("Status", row.status, "sent"),
        *[
            priced_line(x.name, x.quantity, x.unit_cost_minor, x.line_total_minor, row.currency)
            for x in repo.lines(db, ctx, row.id)
        ],
        total_line(row.total_minor, row.currency),
    ]


def run_send(db: Session, ctx: Context, p: ApprovePOPayload, meta: ExecutionMeta) -> dict[str, Any]:
    validate_send(db, ctx, p)
    row = service.transition(db, ctx, p.purchase_order_id, p.version, "send")
    supplier = repo.supplier(db, ctx, row.supplier_id)
    business = org.get_business_brief(db, ctx)
    location = org.get_default_location(db, ctx)
    outbox_id = notifications.enqueue_message(
        db,
        ctx,
        channel=notifications.email_channel(),
        recipient=supplier.email,
        template_key="purchase_order",
        variables={
            "business_name": business.name,
            "number": row.number,
            "date": str(row.approved_date),
            "lines": "\n".join(
                f"{x.name}: {format_quantity(x.quantity)} × "
                f"{money_text(x.unit_cost_minor, row.currency)} = {money_text(x.line_total_minor, row.currency)}"
                for x in repo.lines(db, ctx, row.id)
            ),
            "total": money_text(row.total_minor, row.currency),
            "currency": row.currency,
            "delivery_location": location.name
            + (" · " + ", ".join(str(v) for v in location.address.values()) if location.address else ""),
        },
        idempotency_key=f"po-send:{meta.idempotency_key}",
        related_type="purchase_order",
        related_id=row.id,
        proposal_id=meta.proposal_id,
    )
    return {"purchase_order_id": str(row.id), "outbox_id": str(outbox_id)}


SEND_PO = ExecutorSpec(
    "purchasing.send_po",
    ApprovePOPayload,
    P.APPROVE,
    describe_send,
    validate_send,
    run_send,
    second_person=second_person,
    chat_money=po_money,
)
