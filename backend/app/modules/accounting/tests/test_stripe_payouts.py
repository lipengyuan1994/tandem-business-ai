"""Stripe in the books (M21 phase 4): card payments deposited to a clearing account; each payout a transfer from
clearing to the bank, and Stripe's fees an expense paid from clearing. Synthetic data, fake QuickBooks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.modules.payments.testing import add_payout, set_payout_status
from app.modules.sales.public import record_online_payment
from app.platform.db import transaction
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for

from .fake_quickbooks import FakeQuickBooks
from .helpers import NOW, Shop, issued_invoice, map_accounts, pay, records, sync

ARRIVED = datetime(2026, 10, 30, tzinfo=UTC)


def _stripe_payment(books: Shop) -> None:
    doc = issued_invoice(books)
    ctx = ctx_for(books.business, ["sales.invoice.mark_paid", "sales.document.read"], actor="system")
    with transaction(ctx) as db:
        record_online_payment(
            db, ctx, uuid.UUID(doc["id"]), amount_minor=9000, paid_at=NOW, provider="stripe", external_id="pi_test1"
        )


def _payout(books: Shop, **fields: object) -> uuid.UUID:
    with transaction(ctx_for(books.business)) as db:
        return add_payout(db, books.business, ARRIVED, **fields).id


def test_without_a_clearing_account_stripe_is_left_alone(
    books: Shop, intuit: FakeQuickBooks, registry: Registry
) -> None:
    _stripe_payment(books)
    _payout(books)
    sync(books, registry)
    [payment] = intuit.of("payment")
    assert payment["DepositToAccountRef"] == {"value": "2"}
    assert intuit.of("transfer") == [] and intuit.of("purchase") == []


def test_payout_moves_money_to_the_bank_and_records_stripes_fees(
    books: Shop, intuit: FakeQuickBooks, registry: Registry
) -> None:
    map_accounts(books, stripe_clearing_account_id="5", stripe_fees_account_id="6")
    _stripe_payment(books)
    doc = issued_invoice(books)
    pay(books, doc, 4500, method="cash")  # money taken by hand still goes to the usual deposit account
    _payout(books)
    sync(books, registry)
    deposits = sorted(p["DepositToAccountRef"]["value"] for p in intuit.of("payment"))
    assert deposits == ["2", "5"]
    [transfer] = intuit.of("transfer")
    assert transfer["FromAccountRef"] == {"value": "5"} and transfer["ToAccountRef"] == {"value": "2"}
    assert (transfer["Amount"], transfer["TxnDate"]) == (87.87, "2026-10-30")
    [fees] = intuit.of("purchase")
    assert fees["AccountRef"] == {"value": "5"} and fees["TotalAmt"] == 2.13
    assert fees["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "6"}
    # Sent once.
    writes = len(intuit.writes)
    sync(books, registry)
    assert len(intuit.writes) == writes


def test_a_payout_that_fails_after_it_was_sent_is_removed(
    books: Shop, intuit: FakeQuickBooks, registry: Registry
) -> None:
    map_accounts(books, stripe_clearing_account_id="5", stripe_fees_account_id="6")
    payout_id = _payout(books)
    sync(books, registry)
    assert len(intuit.of("transfer")) == 1
    with transaction(ctx_for(books.business)) as db:
        set_payout_status(db, payout_id, "failed")
    sync(books, registry)
    assert ("transfer", "delete") in {(e, op) for e, op, _ in intuit.writes}
    assert ("purchase", "delete") in {(e, op) for e, op, _ in intuit.writes}


def test_fees_of_a_manual_payout_are_left_to_the_bookkeeper(
    books: Shop, intuit: FakeQuickBooks, registry: Registry
) -> None:
    map_accounts(books, stripe_clearing_account_id="5", stripe_fees_account_id="6")
    _payout(books, automatic=False, summary_complete=False, fees_minor=0)
    sync(books, registry)
    assert len(intuit.of("transfer")) == 1 and intuit.of("purchase") == []
    skipped = [r for r in records(books) if r["entity_type"] == "payout_fees"]
    assert skipped and skipped[0]["status"] == "skipped" and "by hand" in skipped[0]["last_error"]


def test_no_fees_account_holds_the_fees_back(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    map_accounts(books, stripe_clearing_account_id="5")
    _payout(books)
    sync(books, registry)
    assert len(intuit.of("transfer")) == 1 and intuit.of("purchase") == []
    [failed] = [r for r in records(books) if r["entity_type"] == "payout_fees"]
    assert failed["status"] == "failed" and "fees account" in failed["last_error"]


def test_payouts_in_another_currency_are_skipped(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    map_accounts(books, stripe_clearing_account_id="5", stripe_fees_account_id="6")
    _payout(books, currency="EUR")
    sync(books, registry)
    assert intuit.of("transfer") == []
    [skipped] = [r for r in records(books) if r["entity_type"] == "payout_transfer"]
    assert skipped["status"] == "skipped"
