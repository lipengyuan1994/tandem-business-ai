"""What Tandem prepares on its own for online payments (M21 phase 5): pay links in payment reminders, drafted card
refunds for surplus, Dashboard refunds matched to credit notes, and payouts for the briefing. Synthetic data."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.modules.identity.testing import login_as
from app.modules.notifications.templates import render
from app.modules.payments.public import briefing_facts
from app.platform import clock
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.registry import ExecutionMeta
from app.platform.testing.auth import ctx_for

from .conftest import API, World
from .test_pay_links import PAY, _cash, _checkout, _link, _summary
from .test_refunds import APPROVALS, SALES, _credit, _ctx, _decide, _event, _paid_online

REMINDER = "sales.send_payment_reminder"


def _settings(world: World, **values: bool) -> dict[str, Any]:
    r = world.owner.patch(f"{API}/stripe/settings", json={"version": world.status()["version"], **values})
    assert r.status_code == 200, r.text
    return dict(r.json())


def _paid_twice(world: World) -> dict[str, Any]:
    """Cash at the counter, then the customer also pays the whole invoice by card: all of the card money is surplus."""
    world.ready()
    invoice = world.invoice(6300)
    session = _checkout(world, _link(world, invoice))
    world.stripe.pay(session)
    _cash(world, invoice, 6300)
    world.run()
    return invoice


def _overdue(world: World) -> dict[str, Any]:
    """An issued invoice with a customer e-mail, due a month ago."""
    r = world.owner.post(
        "/api/v1/crm/customers",
        json={"display_name": "Bea Example", "contact_points": [{"type": "email", "value": "bea@example.com"}]},
    )
    assert r.status_code == 201, r.text
    item = world.owner.post(
        "/api/v1/catalog/items", json={"name": "Brake pads", "kind": "product", "price_minor": 4200}
    )
    doc = world.owner.post(
        SALES, json={"customer_id": r.json()["id"], "lines": [{"catalog_item_id": item.json()["id"], "quantity": "1"}]}
    ).json()
    doc = world.owner.patch(f"{SALES}/{doc['id']}", json={"version": doc["version"], "due_date": "2026-10-27"}).json()
    r = world.owner.post(f"{SALES}/{doc['id']}/issue", json={"version": doc["version"]})
    assert r.status_code in (200, 201), r.text
    return dict(r.json())


def _send_reminder(world: World, invoice: dict[str, Any]) -> tuple[list[str], str]:
    """Preview and send a reminder as the approvals engine would. Returns the preview's labels and the e-mail body."""
    spec = world.registry.executors[REMINDER]
    payload = spec.payload_model.model_validate(
        {"invoice_id": invoice["id"], "invoice_version": invoice["version"], "body": "Please pay when you can."}
    )
    ctx = ctx_for(world.business, ["sales.document.write"])
    with transaction(ctx) as db:
        preview = spec.describe(db, ctx, payload)
        result = spec.run(db, ctx, payload, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())))
    from app.modules.notifications.models import OutboxMessage

    with transaction(ctx) as db:
        message = db.get(OutboxMessage, uuid.UUID(result["outbox_id"]))
        assert message is not None
        _, body = render(message.template_key, message.variables)
    return [f"{line.label}: {line.after}" for line in preview], body


# -- pay links in reminders ------------------------------------------------------------------------------------------


def test_a_payment_reminder_carries_the_invoices_pay_link(world: World) -> None:
    world.ready()
    invoice = _overdue(world)
    preview, body = _send_reminder(world, invoice)
    assert "Pay online: A pay link for this invoice is added when it is sent" in preview
    link = world.owner.post(f"{API}/invoices/{invoice['id']}/link").json()["url"]  # the same link, made at sending
    assert f"Pay online: {link}" in body and body.startswith("Please pay when you can.")
    page = world.visitor.get(f"{PAY}/{link.rsplit('/', 1)[1]}")
    assert page.status_code == 200 and page.json()["balance_minor"] == 4200


def test_no_pay_link_when_turned_off_or_not_taking_payments(world: World) -> None:
    invoice = _overdue(world)
    _, body = _send_reminder(world, invoice)  # online payments not set up
    assert "Pay online" not in body
    world.ready()
    assert _settings(world, reminder_links=False)["reminder_links"] is False
    invoice = world.get_invoice(invoice["id"])
    preview, body = _send_reminder(world, invoice)
    assert "Pay online" not in body and not any(p.startswith("Pay online") for p in preview)


def test_settings_need_the_manage_permission_and_the_current_version(world: World) -> None:
    world.ready()
    version = world.status()["version"]
    operator = login_as(world.app, world.business, ["operator"])
    assert (
        operator.patch(f"{API}/stripe/settings", json={"version": version, "draft_refunds": False}).status_code == 403
    )
    stale = world.owner.patch(f"{API}/stripe/settings", json={"version": version - 1, "draft_refunds": False})
    assert stale.status_code == 409
    assert _settings(world, draft_refunds=False)["draft_refunds"] is False


# -- drafted surplus refunds -----------------------------------------------------------------------------------------


def test_money_paid_twice_gets_a_card_refund_drafted_for_approval(world: World) -> None:
    invoice = _paid_twice(world)
    summary = _summary(world, invoice)
    [drafted] = summary["refunds"]
    assert (drafted["source"], drafted["status"], drafted["amount_minor"]) == ("surplus", "proposed", 6300)
    assert summary["refund_options"] == [] and not world.stripe.refunds  # nothing sent before approval
    proposal = world.owner.get(f"{APPROVALS}/{drafted['proposal_id']}").json()
    assert proposal["origin_kind"] == "rule" and proposal["status"] == "pending_review"
    assert "Pay 63.00 USD back to the customer's card" in proposal["title"]
    world.run()  # the job does not draft a second one
    assert len(_summary(world, invoice)["refunds"]) == 1
    # Rejecting keeps the money; the refund can still be asked for by hand later.
    assert _decide(world.owner, drafted["proposal_id"], "reject").status_code == 200
    world.run()
    [option] = _summary(world, invoice)["refund_options"]
    assert (option["source"], option["amount_minor"]) == ("surplus", 6300)


def test_no_draft_when_the_business_turned_drafting_off(world: World) -> None:
    world.ready()
    _settings(world, draft_refunds=False)
    invoice = world.invoice(6300)
    session = _checkout(world, _link(world, invoice))
    world.stripe.pay(session)
    _cash(world, invoice, 6300)
    world.run()
    summary = _summary(world, invoice)
    assert summary["refunds"] == [] and [o["source"] for o in summary["refund_options"]] == ["surplus"]


# -- Dashboard refunds matched to credit notes -------------------------------------------------------------------------


def test_a_dashboard_refund_is_matched_to_the_credit_note_issued_for_it(world: World) -> None:
    invoice, intent = _paid_online(world)
    made = world.stripe.dashboard_refund(intent, 6300)
    _event(world, "refund.created", made, "refund")
    world.run()
    assert any("issue a credit note" in t.lower() for t in _notification_bodies(world))
    _credit(world, invoice, "1")  # the owner records it: refund of 63.00 paid back by card
    world.run()
    summary = _summary(world, invoice)
    [row] = summary["refunds"]
    assert row["source"] == "dashboard" and row["credit_note_number"] is not None
    assert summary["refund_options"] == []  # never offered to be paid back a second time


def test_a_dashboard_refund_after_the_credit_note_is_matched_on_arrival(world: World) -> None:
    invoice, intent = _paid_online(world)
    _credit(world, invoice, "1")
    assert [o["source"] for o in _summary(world, invoice)["refund_options"]] == ["credit_note"]
    made = world.stripe.dashboard_refund(intent, 6300)
    _event(world, "refund.created", made, "refund")
    world.run()
    summary = _summary(world, invoice)
    assert summary["refund_options"] == [] and summary["refunds"][0]["credit_note_number"] is not None
    assert any("matched it to the card refund" in b for b in _notification_bodies(world))


def test_a_dashboard_refund_of_another_amount_is_left_for_a_person(world: World) -> None:
    invoice, intent = _paid_online(world)
    made = world.stripe.dashboard_refund(intent, 1000)
    _event(world, "refund.created", made, "refund")
    world.run()
    _credit(world, invoice, "1")
    world.run()
    [row] = _summary(world, invoice)["refunds"]
    assert row["credit_note_number"] is None
    with transaction(_ctx(world)) as db:
        from app.modules.payments.models import PaymentRefund

        assert db.scalars(select(PaymentRefund.invoice_refund_id)).one() is None


# -- the briefing ----------------------------------------------------------------------------------------------------


def test_briefing_facts_list_payouts_and_unmatched_dashboard_refunds(world: World) -> None:
    invoice, intent = _paid_online(world)
    ctx = ctx_for(world.business, ["payments.connection.read"])
    with transaction(ctx) as db:
        facts = briefing_facts(db, ctx)
    assert facts is not None and (facts.arriving, facts.arrived, facts.failed) == ([], [], [])
    soon = world.stripe.payout(world.account_id(), status="in_transit")
    _event(world, "payout.created", soon, "payout")
    made = world.stripe.dashboard_refund(intent, 1000)
    _event(world, "refund.created", made, "refund")
    world.run()
    with transaction(ctx) as db:
        facts = briefing_facts(db, ctx)
    assert facts is not None and [p.status for p in facts.arriving] == ["in_transit"]
    assert facts.dashboard_refunds_to_match == 1
    later = clock.now() + timedelta(days=30)
    clock.set_clock(lambda: later)  # long after it arrived: no longer news
    with transaction(ctx) as db:
        facts = briefing_facts(db, ctx)
    assert facts is not None and facts.arriving == [] and facts.arrived == []
    assert invoice["id"]


def test_briefing_facts_need_payments_read(world: World) -> None:
    world.ready()
    ctx = ctx_for(world.business, ["sales.document.read"])
    with pytest.raises(Forbidden), transaction(ctx) as db:
        briefing_facts(db, ctx)


def _notification_bodies(world: World) -> list[str]:
    return [f"{n['title']} {n.get('body') or ''}" for n in world.owner.get("/api/v1/notifications").json()["items"]]
