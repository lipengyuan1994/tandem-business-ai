"""Stripe fees on paid payments, and payouts to the business's bank (M21 phase 4). Synthetic data."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from app.modules.identity.testing import login_as
from app.modules.payments import payouts, refunds
from app.modules.payments.models import PaymentPayout, PaymentRequest
from app.modules.payments.public import ledger_payout_versions, ledger_payouts
from app.platform.db import transaction

from .conftest import API, World
from .test_pay_links import _summary
from .test_refunds import _ctx, _event, _notifications, _paid_online

FEE = round(6300 * 0.029) + 30  # FakeStripe's card fee on a 63.00 payment


def _payouts(world: World) -> list[dict[str, Any]]:
    r = world.owner.get(f"{API}/payouts")
    assert r.status_code == 200, r.text
    return list(r.json())


def test_stripe_fee_is_read_once_the_payment_is_paid(world: World) -> None:
    invoice, _ = _paid_online(world)
    paid = _summary(world, invoice)["requests"][0]
    assert (paid["stripe_fee_minor"], paid["net_minor"]) == (FEE, 6300 - FEE)
    reads = [c for c in world.stripe.calls if c[0] == "GET" and c[1].startswith("/v1/payment_intents/")]
    assert len(reads) == 1
    # The hourly check leaves payments whose fee is known alone.
    payouts.check_job(_ctx(world), {})
    assert len([c for c in world.stripe.calls if c[0] == "GET" and c[1].startswith("/v1/payment_intents/")]) == 1


def test_a_missing_fee_is_filled_in_by_the_hourly_check(world: World) -> None:
    invoice, _ = _paid_online(world)
    with transaction(_ctx(world)) as db:
        row = db.scalars(select(PaymentRequest)).one()
        row.stripe_fee_minor = row.net_minor = None
    payouts.check_job(_ctx(world), {})
    assert _summary(world, invoice)["requests"][0]["stripe_fee_minor"] == FEE


def test_a_payout_is_tracked_from_in_transit_to_paid_with_stripes_totals(world: World) -> None:
    _paid_online(world)
    account = world.account_id()
    payout_id = world.stripe.payout(account, status="in_transit")
    _event(world, "payout.created", payout_id, "payout")
    world.run()
    [row] = _payouts(world)
    assert (row["status"], row["amount_minor"], row["arrival_date"]) == ("in_transit", 6300 - FEE, "2026-11-29")
    assert row["summarized"] is False
    world.stripe.payouts[payout_id]["status"] = "paid"
    _event(world, "payout.paid", payout_id, "payout")
    world.run()
    [row] = _payouts(world)
    assert row["status"] == "paid" and row["summary_complete"] is True
    assert (row["charges_minor"], row["refunds_minor"], row["fees_minor"], row["transaction_count"]) == (
        6300,
        0,
        FEE,
        1,
    )
    # The books see it once it has arrived.
    ctx = refunds.job_context(_ctx(world))
    with transaction(ctx) as db:
        versions = ledger_payout_versions(db, ctx, since=date(2026, 11, 1))
        [ledger] = ledger_payouts(db, ctx, set(versions)).values()
    assert (ledger.amount_minor, ledger.fees_minor, ledger.status, str(ledger.arrival_on)) == (
        6300 - FEE,
        FEE,
        "paid",
        "2026-11-29",
    )


def test_a_failed_payout_tells_the_owner(world: World) -> None:
    _paid_online(world)
    payout_id = world.stripe.payout(world.account_id(), status="paid")
    _event(world, "payout.paid", payout_id, "payout")
    world.run()
    world.stripe.fail_payout(payout_id)
    _event(world, "payout.failed", payout_id, "payout")
    world.run()
    [row] = _payouts(world)
    assert (row["status"], row["failure_code"]) == ("failed", "account_closed")
    assert any("did not go through" in t for t in _notifications(world.owner))


def test_a_manual_payout_is_shown_without_totals(world: World) -> None:
    _paid_online(world)
    payout_id = world.stripe.payout(world.account_id(), automatic=False)
    _event(world, "payout.paid", payout_id, "payout")
    world.run()
    [row] = _payouts(world)
    assert row["automatic"] is False and row["summarized"] is True and row["summary_complete"] is False
    assert row["fees_minor"] == 0


def test_the_hourly_check_catches_a_lost_payout_event(world: World) -> None:
    _paid_online(world)
    payout_id = world.stripe.payout(world.account_id(), status="in_transit")
    _event(world, "payout.created", payout_id, "payout")
    world.run()
    world.stripe.payouts[payout_id]["status"] = "paid"  # the payout.paid event never arrived
    payouts.check_job(_ctx(world), {})
    [row] = _payouts(world)
    assert row["status"] == "paid" and row["summary_complete"] is True


def test_payout_events_for_unknown_or_malformed_ids_are_ignored(world: World) -> None:
    world.ready()
    assert payouts.apply(_ctx(world), "po_doesnotexist") == "ignored"
    assert payouts.apply(_ctx(world), "re_123") == "ignored"
    with transaction(_ctx(world)) as db:
        assert db.scalars(select(PaymentPayout)).all() == []


def test_payouts_need_the_payments_read_permission(world: World) -> None:
    staff = login_as(world.app, world.business, ["inventory_manager"])
    assert staff.get(f"{API}/payouts").status_code == 403
