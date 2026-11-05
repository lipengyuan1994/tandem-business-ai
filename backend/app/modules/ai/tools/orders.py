"""One next PO proposal: record-backed prices and bounded, authenticated staff quantities."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.modules.inventory import public as inventory
from app.modules.purchasing import public as purchasing
from app.platform.context import Context
from app.platform.errors import Invalid

from .base import RunState, Tool
from .common import ToolArgs, qty
from .proposing import gather, propose, tool_result
from .staff_quantities import RequestedQuantity, choices


class SendPOArgs(ToolArgs):
    purchase_order_id: uuid.UUID | None = None
    draft_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    requested_quantities: list[RequestedQuantity] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def one_subject(self) -> SendPOArgs:
        if not (
            (self.purchase_order_id is not None and self.draft_id is None and self.supplier_id is None)
            or (self.purchase_order_id is None and self.draft_id is not None and self.supplier_id is not None)
        ):
            raise ValueError("Provide purchase_order_id or draft_id plus supplier_id")
        return self


def run(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, SendPOArgs)
    step = purchasing.next_order_step(db, ctx, **args.model_dump(exclude={"requested_quantities"}))
    if args.draft_id is not None:
        draft = inventory.read_reorder_draft(db, ctx, args.draft_id)
        defaults = {line.item_id: line.quantity for line in draft.lines}
    else:
        assert args.purchase_order_id is not None
        defaults = purchasing.order_quantities(db, ctx, args.purchase_order_id)
    selected = choices(
        db,
        ctx,
        state,
        list(defaults),
        args.requested_quantities,
        defaults=defaults,
        reference_id=args.draft_id or args.purchase_order_id,
    )
    changed = any(line.quantity != defaults[line.item_id] for line in selected)
    if args.purchase_order_id is not None and changed:
        raise Invalid(
            "ai.order_quantity_locked", detail="Existing PO quantity differs; revise its draft before approval/send"
        )
    requested = [line for line in selected if line.requested_by is not None]
    if args.draft_id is not None and requested:
        step["payload"]["requested_lines"] = [
            {"item_id": str(line.item_id), "quantity": qty(line.quantity), "reason_text": line.provenance}
            for line in requested
        ]
    own = [tool_result("propose_send_po", args.purchase_order_id or args.draft_id, step)]
    result = propose(
        db, ctx, **step, evidence=gather(db, ctx, state, own, [args.purchase_order_id, args.draft_id, args.supplier_id])
    )

    return {**result, "quantities": [line.result() for line in selected]}


TOOLS = (
    Tool(
        name="propose_send_po",
        kind="propose",
        permission="ai.assistant.use",
        args_model=SendPOArgs,
        run=run,
        description="Propose the next PO step: approved PO sends; draft PO approves; executed draft plus supplier "
        "creates PO. Supply PO id OR draft/supplier ids. Staff may copy requested_quantities (item_id, quantity) "
        "when creating. Existing PO quantities cannot change. Wait for card confirmation and execution.",
    ),
)
