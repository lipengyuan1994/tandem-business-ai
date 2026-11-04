"""Regression checks for payload persistence and exact currency formatting; no database needed."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.platform.money import minor_text

from ..schemas import CreateQuotePayload, DocumentCreate


@pytest.mark.parametrize("rate", [None, "0", "0.0825"])
def test_omitted_and_explicit_tax_survive_proposal_serialization(rate: str | None) -> None:
    line: dict[str, object] = {"catalog_item_id": str(uuid.uuid4()), "quantity": "1"}
    if rate is not None:
        line["tax_rate"] = rate
    body = {"customer_id": str(uuid.uuid4()), "lines": [line]}
    for model in (DocumentCreate, CreateQuotePayload):
        original = model.model_validate(body)
        serialized = original.model_dump(mode="json")
        assert ("tax_rate" in serialized["lines"][0]) == (rate is not None)
        restored = model.model_validate(serialized)
        assert restored == original
        assert restored.lines[0].tax_rate == (Decimal(rate) if rate is not None else None)
        assert model.model_validate_json(original.model_dump_json()) == original


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (100, "JPY", "100"),
        (1, "JPY", "1"),
        (0, "JPY", "0"),
        (100, "USD", "1.00"),
        (1, "EUR", "0.01"),
        (6300, "GBP", "63.00"),
        (100, "KWD", "0.100"),
        (1, "BHD", "0.001"),
        (1234, "OMR", "1.234"),
        (-1, "KWD", "-0.001"),
        (2**63 - 1, "USD", "92233720368547758.07"),
    ],
)
def test_currency_exponents_are_respected(amount: int, currency: str, expected: str) -> None:
    assert minor_text(amount, currency) == expected
