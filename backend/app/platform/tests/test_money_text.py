"""Plain-language money for previews and messages: normal units with the currency, never minor units."""

from __future__ import annotations

import pytest

from app.platform.money import minor_text, money_text


@pytest.mark.parametrize(
    ("amount", "currency", "plain", "with_code"),
    [
        (11800, "USD", "118.00", "118.00 USD"),
        (1180, "USD", "11.80", "11.80 USD"),
        (5, "EUR", "0.05", "0.05 EUR"),
        (0, "GBP", "0.00", "0.00 GBP"),
        (-1250, "USD", "-12.50", "-12.50 USD"),
        (1180, "JPY", "1180", "1180 JPY"),
        (1234, "KWD", "1.234", "1.234 KWD"),
    ],
)
def test_minor_units_become_normal_units_in_the_currencys_own_exponent(
    amount: int, currency: str, plain: str, with_code: str
) -> None:
    assert minor_text(amount, currency) == plain
    assert money_text(amount, currency) == with_code
