"""Relay fees and payouts (phase 4): what a payment cost, and what each payout paid out, on the caller's account."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.relay.testing import FakeStripe

from .conftest import Clock
from .test_checkout import ready
from .test_refunds import paid, refund


def test_a_payments_fee_comes_from_its_balance_transaction(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    caller, account = ready(relay, clock, stripe)
    intent = paid(caller, stripe)
    r = caller.call("GET", f"/v1/payments/{intent}/fee")
    assert r.status_code == 200, r.text
    amount = stripe.intents[intent]["amount"]
    fee = round(amount * 0.029) + 30
    assert r.json() | {"balance_transaction": None} == {
        "payment_intent": intent,
        "charge": f"ch_{intent[3:]}",
        "currency": "usd",
        "amount": amount,
        "balance_transaction": None,
        "fee": fee,
        "net": amount - fee,
    }
    call = next(c for c in stripe.calls if c[1] == f"/v1/payment_intents/{intent}")
    assert call[3]["stripe-account"] == account
    assert caller.call("GET", "/v1/payments/pi_unknown/fee").status_code == 404
    assert caller.call("GET", "/v1/payments/not-an-intent/fee").status_code == 404


def test_a_payout_is_summed_from_every_page_of_its_transactions(
    relay: TestClient, clock: Clock, stripe: FakeStripe
) -> None:
    caller, account = ready(relay, clock, stripe)
    first, second = paid(caller, stripe), paid(caller, stripe)
    caller.call("POST", "/v1/refunds", refund(first, 2_500))
    dispute = stripe.dispute(second, 4_000)
    stripe.page_size = 2  # forces three pages
    payout = stripe.payout(account)
    read = caller.call("GET", f"/v1/payouts/{payout}").json()
    assert (read["status"], read["automatic"], read["arrival_date"]) == ("paid", True, 1795910400)
    summary = caller.call("GET", f"/v1/payouts/{payout}/summary").json()
    gross = stripe.intents[first]["amount"] + stripe.intents[second]["amount"]
    fees = sum(stripe.intents[i]["balance_transaction"]["fee"] for i in (first, second)) + 1500
    assert summary | {"payout": None} == {
        "payout": None,
        "available": True,
        "complete": True,
        "charges": gross,
        "refunds": -2_500,
        "disputes": -4_000,
        "other": 0,
        "fees": fees,
        "count": 4,
    }
    assert summary["payout"]["amount"] == gross - 2_500 - 4_000 - fees
    assert dispute in stripe.disputes


def test_manual_payouts_have_no_summary_and_other_accounts_payouts_are_hidden(
    relay: TestClient, clock: Clock, stripe: FakeStripe
) -> None:
    a, account_a = ready(relay, clock, stripe, "biz-a")
    b, _ = ready(relay, clock, stripe, "biz-b")
    paid(a, stripe)
    manual = stripe.payout(account_a, automatic=False)
    summary = a.call("GET", f"/v1/payouts/{manual}/summary").json()
    assert (summary["available"], summary["complete"], summary["fees"]) == (False, False, 0)
    assert b.call("GET", f"/v1/payouts/{manual}").status_code == 404
    assert b.call("GET", f"/v1/payouts/{manual}/summary").status_code == 404
