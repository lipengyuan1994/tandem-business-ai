"""Per-line decimal arithmetic. Prices and totals are integer minor units throughout."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform.context import Context
from app.platform.errors import Invalid

from .schemas import LineIn, PricedLine, QuotePricing

MAX_MINOR = 2**63 - 1


def line_values(quantity: Decimal, price: int, rate: Decimal) -> tuple[int, int, int]:
    with localcontext() as decimal_ctx:
        decimal_ctx.prec = 40
        subtotal = int((quantity * price).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        tax = int((subtotal * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    total = subtotal + tax
    if total > MAX_MINOR:
        raise Invalid("sales.amount_too_large")
    return subtotal, tax, total


def prepare_lines(db: Session, ctx: Context, inputs: list[LineIn], currency: str) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    default_rate = org.get_sales_tax_rate(db, ctx) if any(line.tax_rate is None for line in inputs) else Decimal("0")
    for position, line in enumerate(inputs):
        name, price = line.name, line.unit_price_minor
        rate = default_rate if line.tax_rate is None else line.tax_rate
        if line.catalog_item_id is not None:
            item = catalog.get_item(db, ctx, line.catalog_item_id)
            if item.status != "active" or item.price_minor is None:
                raise Invalid("sales.item_unavailable")
            if item.currency != currency:
                raise Invalid("sales.currency_mismatch")
            if item.unit == "each" and line.quantity != line.quantity.to_integral_value():
                raise Invalid("sales.fractional_quantity")
            name, price = item.name, item.price_minor
        assert name is not None and price is not None
        subtotal, tax, total = line_values(line.quantity, price, rate)
        prepared.append(
            {
                "catalog_item_id": line.catalog_item_id,
                "name": name,
                "quantity": line.quantity,
                "unit_price_minor": price,
                "tax_rate": rate,
                "line_subtotal_minor": subtotal,
                "line_tax_minor": tax,
                "line_total_minor": total,
                "position": position,
            }
        )
    if sum(row["line_total_minor"] for row in prepared) > MAX_MINOR:
        raise Invalid("sales.amount_too_large")
    return prepared


def pricing_snapshot(currency: str, values: list[dict[str, Any]]) -> QuotePricing:
    return QuotePricing(
        currency=currency,
        lines=[PricedLine.model_validate(line) for line in values],
        subtotal_minor=sum(line["line_subtotal_minor"] for line in values),
        tax_minor=sum(line["line_tax_minor"] for line in values),
        total_minor=sum(line["line_total_minor"] for line in values),
    )
