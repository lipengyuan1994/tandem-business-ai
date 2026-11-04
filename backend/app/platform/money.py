"""Money as integer minor units plus an ISO 4217 currency code. No floats."""

from __future__ import annotations

from dataclasses import dataclass


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError(f"invalid currency code: {self.currency!r}")

    def _check(self, other: Money) -> None:
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def times(self, quantity: int) -> Money:
        return Money(self.amount_minor * quantity, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount_minor < other.amount_minor


# ISO 4217 minor-unit exceptions. Ordinary currencies (USD, EUR, GBP, ...) use two decimals.
_MINOR_UNIT_EXPONENTS = {
    **dict.fromkeys(
        (
            "BIF",
            "CLP",
            "DJF",
            "GNF",
            "ISK",
            "JPY",
            "KMF",
            "KRW",
            "PYG",
            "RWF",
            "UGX",
            "UYI",
            "VND",
            "VUV",
            "XAF",
            "XOF",
            "XPF",
        ),
        0,
    ),
    **dict.fromkeys(("BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"), 3),
}


def currency_exponent(currency: str) -> int:
    """Number of decimal places in an ISO 4217 currency's minor unit (0, 2 or 3)."""
    return _MINOR_UNIT_EXPONENTS.get(currency, 2)


def minor_text(value: int, currency: str) -> str:
    """Integer minor units as a plain decimal in the currency's exponent: 1180 USD -> "11.80", 1180 JPY -> "1180"."""
    exponent = currency_exponent(currency)
    if exponent == 0:
        return str(value)
    major, minor = divmod(abs(value), 10**exponent)
    sign = "-" if value < 0 else ""
    return f"{sign}{major}.{minor:0{exponent}d}"


def money_text(value: int, currency: str) -> str:
    """An amount for people, with its ISO code: 11800 USD -> "118.00 USD". Use this in previews and messages."""
    return f"{minor_text(value, currency)} {currency}"
