"""Human-readable quantity text; keep Decimal values intact in storage and API payloads."""

from decimal import Decimal


def format_quantity(value: Decimal, *, signed: bool = False) -> str:
    text = format(value, "+f" if signed else "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
