"""Card refunds through approvals, refunds made in Stripe's Dashboard, and disputes (M21 phase 3). Synthetic data."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.identity.testing import login_as
from app.modules.payments import refunds
from app.modules.payments.models import PaymentRefund
from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction

from .conftest import API, NOW, World
from .test_pay_links import COMPLETED, _cash, _checkout, _link, _summary

SALES = "/api/v1/sales/invoices"
APPROVALS = "/api/v1/approvals/proposals"


def _ctx(world: World) -> Context:
    return Context(request_id="t", actor_kind="system", user_id=None, business_id=world.business)


def _paid_online(world: World, total: int = 6300) -> tuple[dict[str, Any], str]:
    """An invoice the customer paid in full by card. Returns the invoice and Stripe's payment intent."""
    world.ready()
    invoice = world.invoice(total)
    session = _checkout(world, _link(world, invoice))
    intent = str(world.stripe.pay(session)["payment_intent"])
    world.checkout_event(COMPLETED, session)
    world.run()
    assert world.get_invoice(invoice["id"])["status"] == "paid"
    return invoice, intent


def _credit(world: World, invoice: dict[str, Any], quantity: str, method: str = "card") -> dict[str, Any]:
    doc = world.get_invoice(invoice["id"])
    body = {
        "version": doc["version"],
        "lines": [{"line_id": doc["lines"][0]["id"], "quantity": quantity}],
        "refund": {"paid_on": "2026-11-27", "method": method},
    }
    r = world.owner.post(f"{SALES}/{invoice['id']}/credit", json=body)
    assert r.status_code in (200, 201), r.text
    return world.get_invoice(invoice["id"])


def _event(world: World, kind: str, object_id: str, object_type: str) -> httpx.Response:
    event = world.stripe.event(world.account_id(), kind, {"id": object_id, "object": object_type})
    body, header = world.stripe.signed(event, int(clock.now().timestamp()))
    r = world.relay.post("/v1/stripe/webhook", content=body, headers={"Stripe-Signature": header})
    assert r.json() == {"status": "accepted"}, r.text
    return r


def _ask(client: TestClient, invoice: dict[str, Any], option: dict[str, Any]) -> httpx.Response:
    body = {k: option[k] for k in ("request_id", "source", "invoice_refund_id")}
    return client.post(f"{API}/invoices/{invoice['id']}/card-refunds", json=body)


def _decide(client: TestClient, proposal_id: str, decision: str = "approve") -> httpx.Response:
    proposal = client.get(f"{APPROVALS}/{proposal_id}")
    assert proposal.status_code == 200, proposal.text
    rev = proposal.json()["current_revision"]
    return client.post(
        f"{APPROVALS}/{proposal_id}/decisions",
        json={"decision": decision, "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )


def _notifications(client: TestClient) -> list[str]:
    return [n["title"] for n in client.get("/api/v1/notifications").json()["items"]]


def test_credit_note_refund_waits_for_approval_then_reaches_the_card_once(world: World) -> None:
    invoice, intent = _paid_online(world)
    operator = login_as(world.app, world.business, ["operator"])
    assert _summary(world, invoice)["refund_options"] == []  # nothing is owed back yet

    credited = _credit(world, invoice, "0.5")  # 31.50 credited on a paid invoice: owed back to the customer
    assert credited["amount_refunded_minor"] == 3150
    options = _summary(world, invoice)["refund_options"]
    assert [(o["source"], o["amount_minor"]) for o in options] == [("credit_note", 3150)]

    asked = _ask(operator, invoice, options[0])
    assert asked.status_code == 201, asked.text
    refund = asked.json()["refund"]
    assert (refund["status"], refund["amount_minor"], refund["source"]) == ("proposed", 3150, "credit_note")
    assert refund["credit_note_number"] and not world.stripe.refunds  # nothing sent before approval
    assert _summary(world, invoice)["refund_options"] == []
    again = _ask(operator, invoice, options[0])
    assert (again.status_code, again.json()["code"]) == (409, "payments.refund_exists")

    # Only an owner or admin may approve sending money back.
    assert _decide(operator, asked.json()["proposal_id"]).status_code in (403, 409)
    assert not world.stripe.refunds
    decided = _decide(world.owner, asked.json()["proposal_id"])
    assert decided.status_code == 200, decided.text
    world.run()

    [sent] = world.stripe.refunds.values()
    assert (sent["amount"], sent["payment_intent"], sent["account"]) == (3150, intent, world.account_id())
    assert sent["metadata"]["tandem_refund"] == refund["id"]
    [row] = _summary(world, invoice)["refunds"]
    assert (row["status"], row["id"]) == ("succeeded", refund["id"])

    # Stripe's events, sent more than once, and the job running again change nothing.
    _event(world, "refund.created", sent["id"], "refund")
    _event(world, "refund.updated", sent["id"], "refund")
    refunds.send_job(_ctx(world), {"refund_id": refund["id"]})
    world.run()
    assert len(world.stripe.refunds) == 1
    assert [r["status"] for r in _summary(world, invoice)["refunds"]] == ["succeeded"]
    doc = world.get_invoice(invoice["id"])  # the credit note already put the refund on the ledger
    assert (doc["amount_paid_minor"], doc["amount_refunded_minor"], doc["balance_minor"]) == (6300, 3150, 0)


def test_only_card_refunds_are_offered_and_rejected_proposals_free_the_amount(world: World) -> None:
    invoice, _ = _paid_online(world)
    _credit(world, invoice, "0.2", method="cash")  # paid back in cash at the counter: not for the card
    assert _summary(world, invoice)["refund_options"] == []
    _credit(world, invoice, "0.2")
    [option] = _summary(world, invoice)["refund_options"]
    asked = _ask(world.owner, invoice, option).json()
    admin = login_as(world.app, world.business, ["admin"])
    assert _decide(admin, asked["proposal_id"], "reject").status_code == 200
    world.run()
    assert [r["status"] for r in _summary(world, invoice)["refunds"]] == ["canceled"]
    assert len(_summary(world, invoice)["refund_options"]) == 1  # it can be asked for again
    assert not world.stripe.refunds


def test_a_failed_card_refund_is_shown_and_can_be_asked_for_again(world: World) -> None:
    invoice, _ = _paid_online(world)
    _credit(world, invoice, "1")  # credited in full: the invoice is void and all 63.00 is owed back
    world.stripe.refund_status = "pending"
    [option] = _summary(world, invoice)["refund_options"]
    asked = _ask(world.owner, invoice, option).json()
    _decide(login_as(world.app, world.business, ["admin"]), asked["proposal_id"])
    world.run()
    [sent] = world.stripe.refunds.values()
    assert [r["status"] for r in _summary(world, invoice)["refunds"]] == ["pending"]

    world.stripe.finish_refund(sent["id"], ok=False)
    _event(world, "refund.failed", sent["id"], "refund")
    world.run()
    [row] = _summary(world, invoice)["refunds"]
    assert (row["status"], row["failure_reason"]) == ("failed", "expired_or_canceled_card")
    assert any("did not go through" in t for t in _notifications(world.owner))
    [option] = _summary(world, invoice)["refund_options"]
    assert option["amount_minor"] == 6300
    assert _ask(world.owner, invoice, option).status_code == 201


def test_surplus_can_be_paid_back_and_the_relay_outage_is_retried(world: World) -> None:
    world.ready()
    invoice = world.invoice(6300)
    session = _checkout(world, _link(world, invoice))
    world.stripe.pay(session)
    _cash(world, invoice, 6300)  # paid twice: cash at the counter, card online
    world.run()
    # Tandem drafts the surplus refund for approval on its own (phase 5); nothing else is offered meanwhile.
    summary = _summary(world, invoice)
    [drafted] = summary["refunds"]
    assert (drafted["source"], drafted["amount_minor"], summary["refund_options"]) == ("surplus", 6300, [])
    asked = {"proposal_id": drafted["proposal_id"]}
    world.stripe.fail_next.append(httpx.Response(503, json={"error": {"type": "api_error", "message": "down"}}))
    _decide(login_as(world.app, world.business, ["admin"]), asked["proposal_id"])
    world.run()
    assert not world.stripe.refunds and [r["status"] for r in _summary(world, invoice)["refunds"]] == ["sending"]
    clock.set_clock(lambda: NOW + timedelta(minutes=2))  # past the job's back-off
    world.run()
    assert len(world.stripe.refunds) == 1  # the job tried again after Stripe was down
    assert [r["status"] for r in _summary(world, invoice)["refunds"]] == ["succeeded"]
    assert _summary(world, invoice)["refund_options"] == []
    doc = world.get_invoice(invoice["id"])
    assert (doc["status"], doc["amount_paid_minor"]) == ("paid", 6300)  # the ledger never saw the surplus


def test_a_dashboard_refund_is_recorded_and_reported_but_the_invoice_is_left_alone(world: World) -> None:
    invoice, intent = _paid_online(world)
    made = world.stripe.dashboard_refund(intent, 1000)
    _event(world, "refund.created", made, "refund")
    _event(world, "refund.created", made, "refund")
    world.run()
    [row] = _summary(world, invoice)["refunds"]
    assert (row["source"], row["status"], row["amount_minor"]) == ("dashboard", "succeeded", 1000)
    doc = world.get_invoice(invoice["id"])
    assert (doc["status"], doc["balance_minor"], doc["amount_refunded_minor"]) == ("paid", 0, 0)
    assert any("was made in Stripe" in t for t in _notifications(world.owner))
    # What is left to pay back by card now leaves room for the Dashboard refund.
    _credit(world, invoice, "1")
    assert _summary(world, invoice)["refund_options"] == []  # 63.00 is owed, only 53.00 is left on the payment
    # A refund for a payment Tandem does not know is ignored.
    _event(world, "refund.created", "re_unknown123", "refund")
    world.run()
    assert len(_summary(world, invoice)["refunds"]) == 1


def test_a_lost_dispute_reopens_the_invoice_once(world: World) -> None:
    invoice, intent = _paid_online(world)
    dispute = world.stripe.dispute(intent)
    _event(world, "charge.dispute.created", dispute, "dispute")
    world.run()
    [row] = _summary(world, invoice)["disputes"]
    assert (row["outcome"], row["amount_minor"], row["stripe_status"]) == ("open", 6300, "needs_response")
    assert row["due_by"] is not None
    assert any("is disputed" in t for t in _notifications(world.owner))
    assert world.get_invoice(invoice["id"])["status"] == "paid"  # nothing changes while it is open

    world.stripe.close_dispute(dispute, won=False)
    _event(world, "charge.dispute.closed", dispute, "dispute")
    _event(world, "charge.dispute.updated", dispute, "dispute")
    world.run()
    [row] = _summary(world, invoice)["disputes"]
    assert (row["outcome"], row["reversed_minor"]) == ("lost", 6300)
    doc = world.get_invoice(invoice["id"])
    assert (doc["status"], doc["balance_minor"], doc["amount_paid_minor"]) == ("issued", 6300, 0)
    assert [p["voided_at"] is not None for p in doc["payments"]] == [True]
    assert world.visitor.get(f"{API}/pay/{_link(world, invoice)}").json()["state"] == "payable"


def test_a_lost_dispute_after_a_card_refund_keeps_the_ledger_true(world: World) -> None:
    invoice, intent = _paid_online(world)
    _credit(world, invoice, "0.5")
    [option] = _summary(world, invoice)["refund_options"]
    _decide(login_as(world.app, world.business, ["admin"]), _ask(world.owner, invoice, option).json()["proposal_id"])
    world.run()
    dispute = world.stripe.dispute(intent)  # disputes what is left: 31.50
    _event(world, "charge.dispute.created", dispute, "dispute")
    world.run()
    assert _summary(world, invoice)["refund_options"] == []
    world.stripe.close_dispute(dispute, won=False)
    _event(world, "charge.dispute.closed", dispute, "dispute")
    world.run()
    doc = world.get_invoice(invoice["id"])
    # Paid 63.00, 31.50 credited and refunded by card, 31.50 taken back by the bank: 31.50 is owed again.
    assert (doc["amount_paid_minor"], doc["amount_refunded_minor"], doc["amount_credited_minor"]) == (3150, 3150, 3150)
    assert (doc["status"], doc["balance_minor"]) == ("issued", 3150)
    assert sorted((p["amount_minor"], p["voided_at"] is None) for p in doc["payments"]) == [
        (3150, True),
        (6300, False),
    ]


def test_a_won_dispute_only_clears_the_flag(world: World) -> None:
    invoice, intent = _paid_online(world)
    dispute = world.stripe.dispute(intent)
    _event(world, "charge.dispute.created", dispute, "dispute")
    world.run()
    world.stripe.close_dispute(dispute, won=True)
    _event(world, "charge.dispute.closed", dispute, "dispute")
    world.run()
    [row] = _summary(world, invoice)["disputes"]
    assert (row["outcome"], row["reversed_minor"]) == ("won", 0)
    assert world.get_invoice(invoice["id"])["status"] == "paid"


def test_who_may_ask_for_card_refunds(world: World) -> None:
    invoice, _ = _paid_online(world)
    _credit(world, invoice, "0.5")
    [option] = _summary(world, invoice)["refund_options"]
    for role in ("auditor", "inventory_manager", "marketing_manager"):
        client = login_as(world.app, world.business, [role])
        assert _ask(client, invoice, option).status_code == 403, role
    bogus = dict(option, source="surplus", invoice_refund_id=None)
    r = _ask(world.owner, invoice, bogus)
    assert (r.status_code, r.json()["code"]) == (409, "payments.refund_too_large")


def test_the_hourly_check_sends_a_stuck_refund(world: World) -> None:
    invoice, _ = _paid_online(world)
    _credit(world, invoice, "0.5")
    [option] = _summary(world, invoice)["refund_options"]
    asked = _ask(world.owner, invoice, option).json()
    with transaction(_ctx(world)) as db:  # approved, but the send job never ran (worker lost it)
        row = db.scalar(select(PaymentRefund).where(PaymentRefund.id == asked["refund"]["id"]))
        assert row is not None
        row.status, row.sent_at = "sending", NOW
    clock.set_clock(lambda: NOW + timedelta(minutes=20))
    refunds.check_job(_ctx(world), {})
    world.run()
    assert len(world.stripe.refunds) == 1
    assert [r["status"] for r in _summary(world, invoice)["refunds"]] == ["succeeded"]
