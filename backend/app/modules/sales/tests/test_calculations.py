"""Deterministic checks that also run without the PostgreSQL test plugin."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.platform.errors import Invalid
from app.platform.money import minor_text

from ..calculations import line_values
from ..schemas import DocumentUpdate, LineIn
from ..service_creation import csv_text


@pytest.mark.parametrize(
    ("quantity", "price", "rate", "expected"),
    [
        ("3.335", 300, "0.0825", (1001, 83, 1084)),
        ("1.5", 3, "0.1", (5, 1, 6)),
        ("3", 3335, "0.0825", (10005, 825, 10830)),
        ("0.001", 1, "1", (0, 0, 0)),
        ("1", 0, "0.0825", (0, 0, 0)),
    ],
)
def test_line_rounding(quantity: str, price: int, rate: str, expected: tuple[int, int, int]) -> None:
    assert line_values(Decimal(quantity), price, Decimal(rate)) == expected


def test_amount_overflow_is_a_domain_error() -> None:
    with pytest.raises(Invalid, match=r"sales\.amount_too_large"):
        line_values(Decimal("99999999999.999"), 10**12, Decimal("1"))


@pytest.mark.parametrize(
    "changes",
    [
        {"unit_price_minor": 3.335},
        {"quantity": "0"},
        {"quantity": 1.5},
        {"tax_rate": 0.0825},
        {"quantity": "1.0001"},
        {"tax_rate": "1.0001"},
        {"tax_rate": "NaN"},
        {"unit_price_minor": -1},
    ],
)
def test_invalid_line_inputs(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LineIn.model_validate({"name": "Service", "quantity": "1", "unit_price_minor": 100, **changes})


def test_update_cannot_null_the_lines() -> None:
    with pytest.raises(ValidationError):
        DocumentUpdate(version=1, lines=None)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ada Example", "Ada Example"),
        ("=SUM(A1)", "'=SUM(A1)"),
        ("  +test", "'  +test"),
    ],
)
def test_csv_formula_protection(name: str, expected: str) -> None:
    assert csv_text(name) == expected


def test_csv_money_is_exact() -> None:
    assert minor_text(1, "USD") == "0.01"
    assert minor_text(6300, "USD") == "63.00"
    assert minor_text(2**63 - 1, "USD") == "92233720368547758.07"


def test_catalog_line_accepts_explicit_tax_and_distinguishes_zero_from_default() -> None:
    item_id = uuid.uuid4()
    assert LineIn(catalog_item_id=item_id, quantity=Decimal("1")).tax_rate is None
    assert LineIn(catalog_item_id=item_id, quantity=Decimal("1"), tax_rate=Decimal("0")).tax_rate == 0
    explicit = LineIn(catalog_item_id=item_id, quantity=Decimal("1"), tax_rate=Decimal("0.0825"))
    assert explicit.tax_rate == Decimal("0.0825")


def test_explicit_null_tax_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LineIn(catalog_item_id=uuid.uuid4(), quantity=Decimal("1"), tax_rate=None)
