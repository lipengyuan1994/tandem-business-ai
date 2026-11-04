"""Formatting must not lose fractional units or rewrite whole tens."""

from decimal import Decimal

import pytest

from app.platform.quantities import format_quantity


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.000", "1"),
        ("2.500", "2.5"),
        ("10.000", "10"),
        ("0.000", "0"),
        ("-2.050", "-2.05"),
        ("0.001", "0.001"),
        ("99999999999.999", "99999999999.999"),
        ("1E+3", "1000"),
    ],
)
def test_format_quantity(raw: str, expected: str) -> None:
    assert format_quantity(Decimal(raw)) == expected


@pytest.mark.parametrize(("raw", "expected"), [("1.000", "+1"), ("-2.500", "-2.5"), ("0.000", "+0")])
def test_signed_quantity(raw: str, expected: str) -> None:
    assert format_quantity(Decimal(raw), signed=True) == expected
