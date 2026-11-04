"""Approval preview text, written for the person who approves: item names, plain quantities and money in normal units
with the currency. Never an id and never minor units (the payloads keep both; the preview is only for reading)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.approvals.public import PreviewLine
from app.modules.catalog import public as catalog
from app.platform.context import Context
from app.platform.money import money_text
from app.platform.quantities import format_quantity


def item_names(db: Session, ctx: Context, item_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    return {item_id: item.name for item_id, item in catalog.get_items(db, ctx, item_ids).items()}


def item_name(names: dict[uuid.UUID, str], item_id: uuid.UUID) -> str:
    return names.get(item_id, "Unknown item")


def priced_line(
    name: str, quantity: Decimal, unit_cost_minor: int, line_total_minor: int, currency: str
) -> PreviewLine:
    """One line per item: "Brake pads (pair)" -> "10 × 11.80 USD = 118.00 USD"."""
    unit, total = money_text(unit_cost_minor, currency), money_text(line_total_minor, currency)
    return PreviewLine(name, None, f"{format_quantity(quantity)} × {unit} = {total}")


def total_line(total_minor: int, currency: str) -> PreviewLine:
    return PreviewLine("Total", None, money_text(total_minor, currency))
