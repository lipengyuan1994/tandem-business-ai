"""Date-level cash availability for recording refunds on locked ledger documents."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date


def refunds_covered(payments: Iterable[tuple[date, int]], refunds: Iterable[tuple[date, int]]) -> bool:
    """Payments on the same date count; every refund day must be covered, including later recorded refunds."""
    daily: dict[date, int] = defaultdict(int)
    for paid_on, amount in payments:
        daily[paid_on] += amount
    for refunded_on, amount in refunds:
        daily[refunded_on] -= amount
    balance = 0
    for day in sorted(daily):
        balance += daily[day]
        if balance < 0:
            return False
    return True
