"""Inventory read tools: check_stock, list_low_stock (contracts §10, integration-map §B).

Quantities are strings computed by inventory; reorder quantities (`suggested`) are the ones `propose_reorder` uses.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.inventory import public as inventory
from app.platform.context import Context
from app.platform.errors import NotFound

from .base import RunState, Tool
from .common import NoArgs, ToolArgs, cite, listing, qty, redact_contacts

READ = "inventory.stock.read"


class CheckStockArgs(ToolArgs):
    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)

    @field_validator("item_ids")
    @classmethod
    def unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))


def suggestion_view(s: inventory.ReorderSuggestion) -> dict[str, Any]:
    return {
        "item_id": s.item_id, "item_name": s.item_name, "available": qty(s.available),
        "reorder_point": qty(s.reorder_point), "target_level": qty(s.target_level), "suggested": qty(s.suggested),
        "explanation": redact_contacts(s.explanation, 300),
    }  # fmt: skip


def _check_stock(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, CheckStockArgs)
    known = catalog.get_items(db, ctx, set(args.item_ids))
    missing = [i for i in args.item_ids if i not in known]
    if missing:
        raise NotFound("catalog.item_not_found", detail=", ".join(str(i) for i in missing))
    rows = inventory.get_availability(db, ctx, args.item_ids)
    tracked = {a.item_id for a in rows}
    data = listing(
        "items",
        [
            {"item_id": a.item_id, "item_name": a.item_name, "unit": a.unit, "on_hand": qty(a.on_hand),
             "reserved": qty(a.reserved), "available": qty(a.available), "reorder_point": qty(a.reorder_point),
             "below_reorder": a.below_reorder}
            for a in rows
        ],
    )  # fmt: skip
    untracked = [i for i in args.item_ids if i not in tracked]
    if untracked:
        data["not_stock_tracked"] = untracked
    cite(state, "check_stock", args.item_ids[0] if len(args.item_ids) == 1 else None, data)
    return data


def _list_low_stock(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    data = listing("items", [suggestion_view(s) for s in inventory.list_below_reorder(db, ctx)])
    cite(state, "list_low_stock", None, data)
    return data


TOOLS = (
    Tool(
        name="check_stock",
        description="Stock of up to 20 items: on hand, reserved, available, reorder point, whether below it.",
        kind="read", permission=READ, args_model=CheckStockArgs, run=_check_stock,
    ),
    Tool(
        name="list_low_stock",
        description="Items below their reorder point with the computed reorder quantity ('suggested') and why.",
        kind="read", permission=READ, args_model=NoArgs, run=_list_low_stock,
    ),
)  # fmt: skip
