"""Non-AI adapters: immutable proposal revisions bind conversion costs and document versions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.catalog import public as catalog
from app.modules.inventory import public as inventory
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import repository as repo
from . import service
from .delivery import draft_money
from .models import PurchaseOrder
from .previews import priced_line, total_line
from .schemas import ApprovePOPayload, ConversionSnapshot, CreateFromDraftPayload, DraftConversion, POCreate, POLineIn


def snapshot(db: Session, ctx: Context, p: DraftConversion, *, lock: bool = False) -> ConversionSnapshot:
    draft = inventory.read_reorder_draft(db, ctx, p.draft_id, lock=lock)
    if draft.version != p.version or draft.status != "draft":
        raise Conflict("purchasing.draft_changed")
    supplier = repo.supplier(db, ctx, p.supplier_id, lock=lock)
    items = {i.catalog_item_id: i for i in repo.supplier_items(db, ctx, supplier.id)}
    if not draft.lines or any(line.item_id not in items for line in draft.lines):
        raise Invalid("purchasing.supplier_item_required")
    overrides = (
        {line.item_id: line.quantity for line in p.requested_lines}
        if isinstance(p, CreateFromDraftPayload) and p.requested_lines
        else {}
    )
    if isinstance(p, CreateFromDraftPayload) and p.requested_lines and len(overrides) != len(p.requested_lines):
        raise Invalid("purchasing.duplicate_requested_item")
    if set(overrides) - {line.item_id for line in draft.lines}:
        raise Invalid("purchasing.requested_item_not_in_draft")
    values = service.prepare_lines(
        db,
        ctx,
        [
            POLineIn(
                catalog_item_id=line.item_id,
                quantity=overrides.get(line.item_id, line.quantity),
                unit_cost_minor=items[line.item_id].last_price_minor,
            )
            for line in draft.lines
        ],
    )
    return ConversionSnapshot(
        supplier_name=supplier.name,
        currency=catalog.get_default_currency(db, ctx),
        notes=draft.note,
        lines=values,
        total_minor=sum(line.line_total_minor for line in values),
    )


def from_draft(
    db: Session, ctx: Context, p: DraftConversion, *, expected: ConversionSnapshot | None = None
) -> PurchaseOrder:
    ctx.require(P.WRITE)
    current = snapshot(db, ctx, p, lock=True)
    if expected is not None and current != expected:
        raise Conflict("purchasing.preview_changed", detail="Supplier, items or prices changed; revise the proposal")
    row = service.create(
        db,
        ctx,
        POCreate(
            supplier_id=p.supplier_id,
            notes=current.notes,
            lines=[
                POLineIn(**line.model_dump(include={"catalog_item_id", "quantity", "unit_cost_minor"}))
                for line in current.lines
            ],
        ),
        expected=current,
    )
    row.source_draft_id = p.draft_id
    inventory.mark_reorder_draft_converted(db, ctx, p.draft_id, p.version)
    service.record(db, ctx, row, "from_draft")
    return row


def validate_create(db: Session, ctx: Context, p: CreateFromDraftPayload) -> None:
    p.snapshot = snapshot(db, ctx, p)


def describe_create(db: Session, ctx: Context, p: CreateFromDraftPayload) -> list[PreviewLine]:
    if p.snapshot is None:
        raise Conflict("purchasing.preview_required")
    currency = p.snapshot.currency
    return [
        PreviewLine("Supplier", None, p.snapshot.supplier_name),
        *[
            priced_line(line.name, line.quantity, line.unit_cost_minor, line.line_total_minor, currency)
            for line in p.snapshot.lines
        ],
        total_line(p.snapshot.total_minor, currency),
        *[PreviewLine("Quantity source", None, line.reason_text) for line in (p.requested_lines or [])],
        *[
            PreviewLine("Quantity source", None, line.reason_text)
            for line in inventory.read_reorder_draft(db, ctx, p.draft_id).lines
            if line.reason_text and not p.requested_lines
        ],
    ]


def run_create(db: Session, ctx: Context, p: CreateFromDraftPayload, meta: ExecutionMeta) -> dict[str, Any]:
    if p.snapshot is None:
        raise Conflict("purchasing.preview_required")
    return {"purchase_order_id": str(from_draft(db, ctx, p, expected=p.snapshot).id)}


def validate_approve(db: Session, ctx: Context, p: ApprovePOPayload) -> None:
    row = repo.order(db, ctx, p.purchase_order_id)
    service.check_version(row, p.version)
    if row.status != "draft":
        raise Conflict("purchasing.invalid_transition")


def describe_approve(db: Session, ctx: Context, p: ApprovePOPayload) -> list[PreviewLine]:
    row = repo.order(db, ctx, p.purchase_order_id)
    return [
        PreviewLine("Supplier", None, row.supplier_name),
        PreviewLine("Status", row.status, "approved"),
        *[
            priced_line(line.name, line.quantity, line.unit_cost_minor, line.line_total_minor, row.currency)
            for line in repo.lines(db, ctx, row.id)
        ],
        total_line(row.total_minor, row.currency),
    ]


def run_approve(db: Session, ctx: Context, p: ApprovePOPayload, meta: ExecutionMeta) -> dict[str, Any]:
    return {"purchase_order_id": str(service.transition(db, ctx, p.purchase_order_id, p.version, "approve").id)}


CREATE_FROM_DRAFT = ExecutorSpec(
    "purchasing.create_po_from_draft",
    CreateFromDraftPayload,
    P.APPROVE,
    describe_create,
    validate_create,
    run_create,
    chat_money=draft_money,
)
APPROVE_PO = ExecutorSpec(
    "purchasing.approve_po", ApprovePOPayload, P.APPROVE, describe_approve, validate_approve, run_approve
)
