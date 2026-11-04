"""Cost of one model call in micro-USD (USD × 10⁶), from the route entry's prices. Contracts §6."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .routes import RouteEntry
from .types import Usage


def cost_micros(entry: RouteEntry, usage: Usage) -> int:
    """price per million tokens × tokens = micro-USD per token × tokens; rounded half up to a whole micro-USD."""
    total = (
        Decimal(str(entry.price_in_per_m)) * usage.input_tokens
        + Decimal(str(entry.price_out_per_m)) * usage.output_tokens
    )
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


__all__ = ["cost_micros"]
