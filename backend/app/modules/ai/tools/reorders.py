"""Reorder proposals: suggestions for background runs, explicit copied quantities for staff copilot."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.inventory import public as inventory
from app.platform.context import Context
from app.platform.errors import Conflict

from .base import RunState, Tool
from .common import ToolArgs, qty
from .proposing import PlainText, gather, propose, tool_result
from .staff_quantities import RequestedQuantity, choices


class ProposeReorderArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("item_ids", "requested_quantities")
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    requested_quantities: list[RequestedQuantity] = Field(default_factory=list, max_length=20)
    note: PlainText | None = Field(default=None, max_length=1000)
    reason_text: PlainText | None = Field(default=None, max_length=300)

    @field_validator("item_ids")
    @classmethod
    def unique_sorted(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return sorted(set(value))


def propose_reorder(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposeReorderArgs)
    for item_id in args.item_ids:
        if inventory.has_open_reorder_draft(db, ctx, item_id):
            raise Conflict("inventory.open_reorder_draft")
        if approvals.find_open_proposals(
            db, ctx, action_types=["inventory.create_reorder_draft"], item_id=item_id, include_executing=True
        ):
            raise Conflict("ai.reorder_already_proposed")
    selected = choices(db, ctx, state, args.item_ids, args.requested_quantities)
    payload: dict[str, Any] = {
        "lines": [
            {
                "item_id": str(line.item_id),
                "quantity": qty(line.quantity),
                "reason_text": (
                    (line.provenance + (f" · {args.reason_text}" if args.reason_text else ""))[:300]
                    if line.requested_by is not None
                    else args.reason_text or line.explanation[:300]
                ),
            }
            for line in selected
        ]
    }
    if args.note is not None:
        payload["note"] = args.note
    parts = [f"{line.name} ×{qty(line.quantity)}" for line in selected]
    title = f"Reorder {parts[0]}" if len(parts) == 1 else f"Reorder {len(parts)} items: {', '.join(parts)}"
    quantities = [line.result() for line in selected]
    result = propose(
        db,
        ctx,
        action_type="inventory.create_reorder_draft",
        payload=payload,
        title=title,
        evidence=gather(db, ctx, state, [tool_result("propose_reorder", None, quantities)], args.item_ids),
    )
    return {**result, "quantities": quantities}


TOOL = Tool(
    name="propose_reorder",
    kind="propose",
    permission="ai.assistant.use",
    args_model=ProposeReorderArgs,
    run=propose_reorder,
    description="Propose a reorder draft. Staff copilot: copy explicit item quantities into "
    "requested_quantities (item_id, quantity); never substitute a suggestion. Otherwise use computed suggestions. "
    "Only active stock products; staff quantity >0 and ≤10000, max 3 decimals. Optional note/reason_text.",
)
