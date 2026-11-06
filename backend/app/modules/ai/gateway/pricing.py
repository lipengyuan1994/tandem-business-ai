"""Cost of one model call in micro-USD (USD × 10⁶), from the route entry's prices. Contracts §6."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from .routes import RouteEntry
from .types import Usage


def cost_micros(entry: RouteEntry, usage: Usage, *, round_up: bool = False) -> int:
    """price per million tokens × tokens = micro-USD per token × tokens; rounded half up to a whole micro-USD."""
    if usage.audio_seconds is not None:
        # Optional for legacy routes; never interpret an absent audio price as free. Operator must confirm.
        audio_price = entry.price_per_audio_minute if entry.price_per_audio_minute is not None else 0.006
        total = Decimal(str(audio_price)) * Decimal(str(usage.audio_seconds)) * 1_000_000 / 60
        return int(total.quantize(Decimal(1), rounding=ROUND_CEILING if round_up else ROUND_HALF_UP))
    total = (
        Decimal(str(entry.price_in_per_m)) * usage.input_tokens
        + Decimal(str(entry.price_out_per_m)) * usage.output_tokens
    )
    return int(total.quantize(Decimal(1), rounding=ROUND_CEILING if round_up else ROUND_HALF_UP))


__all__ = ["cost_micros"]
