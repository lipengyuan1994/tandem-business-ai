"""Public, permission-gated planning of one next proposal; never creates a PO or decides."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.inventory import public as inventory
from app.platform.context import Context
from app.platform.errors import Invalid

from . import repository as repo
from .schemas import CreateFromDraftPayload


def next_order_step(
    db: Session,
    ctx: Context,
    *,
    purchase_order_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    ctx.require("purchasing.po.read")
    if purchase_order_id is not None:
        row = repo.order(db, ctx, purchase_order_id)
        action = {"draft": "purchasing.approve_po", "approved": "purchasing.send_po"}.get(row.status)
        if action is None:
            raise Invalid("purchasing.no_next_order_step", detail="PO is already sent, received or cancelled")
        return {
            "action_type": action,
            "payload": {"purchase_order_id": str(row.id), "version": row.version},
            "title": (
                f"{'Send' if row.status == 'approved' else 'Approve'} PO {row.number or 'draft'} · {row.supplier_name}"
            ),
        }
    if draft_id is None or supplier_id is None:
        raise Invalid(
            "purchasing.order_reference_required", detail="Provide a PO, or an executed reorder draft and supplier"
        )
    draft = inventory.read_reorder_draft(db, ctx, draft_id)
    supplier = repo.supplier(db, ctx, supplier_id)
    data = CreateFromDraftPayload(draft_id=draft.id, version=draft.version, supplier_id=supplier.id)
    return {
        "action_type": "purchasing.create_po_from_draft",
        "payload": data.model_dump(mode="json"),
        "title": f"Create PO · {supplier.name}",
    }
