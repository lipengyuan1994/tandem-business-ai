from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.inventory import public as inventory
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import repository as repo
from . import service
from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder
from .schemas import ReceiptIn


def receive(db: Session, ctx: Context, row_id: uuid.UUID, data: ReceiptIn) -> PurchaseOrder:
    ctx.require(P.RECEIVE)
    if data.override:
        ctx.require(P.OVERRIDE)
    row = repo.order(db, ctx, row_id, lock=True)
    service.check_version(row, data.version)
    if row.status not in ("sent", "partially_received"):
        raise Conflict("purchasing.invalid_transition", detail="Receive goods after marking the approved PO as sent")
    lines = {line.id: line for line in repo.lines(db, ctx, row.id)}
    for received in data.lines:
        line = lines.get(received.po_line_id)
        if line is None:
            raise Invalid("purchasing.invalid_receipt_line")
        if line.received_quantity + received.quantity > line.quantity * Decimal("1.1") and not data.override:
            raise Conflict("purchasing.over_receipt", detail="Receipt exceeds 110% of the ordered quantity")
    receipt = GoodsReceipt(
        created_at=clock.now(),
        business_id=ctx.business_id,
        purchase_order_id=row.id,
        received_by=ctx.user_id,
        override_reason=data.reason.strip() if data.override and data.reason else None,
    )
    db.add(receipt)
    db.flush()
    # Global item order prevents deadlocks for simultaneous multi-item receipts across different POs.
    for received in sorted(data.lines, key=lambda r: (lines[r.po_line_id].catalog_item_id, r.po_line_id)):
        line = lines[received.po_line_id]
        inventory.record_movement(
            db,
            ctx,
            inventory.MovementIn(
                item_id=line.catalog_item_id,
                quantity=received.quantity,
                reason="receipt",
                ref_type="goods_receipt",
                ref_id=receipt.id,
                unit_cost_minor=line.unit_cost_minor,
            ),
        )
        db.add(
            GoodsReceiptLine(
                business_id=ctx.business_id, receipt_id=receipt.id, po_line_id=line.id, quantity=received.quantity
            )
        )
        line.received_quantity += received.quantity
        line.version += 1
    row.status = (
        "received" if all(line.received_quantity >= line.quantity for line in lines.values()) else "partially_received"
    )
    row.version += 1
    service.record(db, ctx, row, "received")
    audit.record(
        db,
        ctx,
        action="purchasing.receipt.created",
        target_type="goods_receipt",
        target_id=receipt.id,
        summary="Goods received",
        changes={
            "purchase_order_id": str(row.id),
            "override": data.override,
            "override_reason": receipt.override_reason,
        },
    )
    return row
