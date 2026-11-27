"""Pay links, the public pay page, Stripe Checkout and payments on invoices (M21 phase 2). Synthetic data only."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.modules.identity.testing import login_as
from app.modules.payments import paylinks
from app.modules.payments.models import PaymentRequest
from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction

from .conftest import API, NOW, World

PAY = f"{API}/pay"
COMPLETED, EXPIRED = "checkout.session.completed", "checkout.session.expired"
ASYNC_OK, ASYNC_FAILED = "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed"


def _ctx(world: World) -> Context:
    return Context(request_id="t", actor_kind="system", user_id=None, business_id=world.business)


def _link(world: World, invoice: dict[str, Any]) -> str:
    r = world.owner.post(f"{API}/invoices/{invoice['id']}/link")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    url = str(r.json()["url"])
    assert url.startswith("https://localhost:8443/pay/")
    return url.rsplit("/", 1)[1]


def _checkout(world: World, token: str) -> str:
    r = world.visitor.post(f"{PAY}/{token}/checkout")
    assert r.status_code == 200, r.text
    url = str(r.json()["url"])
    assert url.startswith("https://checkout.stripe.com/")
    return url.rsplit("/", 1)[1]


def _summary(world: World, invoice: dict[str, Any]) -> dict[str, Any]:
    r = world.owner.get(f"{API}/invoices/{invoice['id']}")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _cash(world: World, invoice: dict[str, Any], amount: int) -> None:
    doc = world.get_invoice(invoice["id"])
    r = world.owner.post(
        f"/api/v1/sales/invoices/{doc['id']}/payments",
        json={"version": doc["version"], "amount_minor": amount, "paid_on": "2026-11-27", "method": "cash"},
    )
    assert r.status_code == 201, r.text


def test_customer_pays_by_card_and_the_invoice_is_paid_once(world: World) -> None:
    world.ready()
    invoice = world.invoice(6300)
    token = _link(world, invoice)
    assert _link(world, invoice) == token  # the same link every time it is copied

    page = world.visitor.get(f"{PAY}/{token}")
    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.json() | {"issue_date": None} == {
        "business_name": page.json()["business_name"],
        "invoice_number": invoice["number"],
        "currency": invoice["currency"],
        "total_minor": 6300,
        "balance_minor": 6300,
        "issue_date": None,
        "due_date": invoice["due_date"],
        "state": "payable",
        "test_mode": True,
    }

    session = _checkout(world, token)
    stored = world.stripe.sessions[session]
    assert stored["amount_total"] == 6300 and stored["payment_method_types"] == ["card"]
    assert stored["account"] == world.account_id()
    assert stored["success_url"] == f"https://localhost:8443/pay/{token}?paid=1"
    assert stored["metadata"]["tandem_invoice"] == invoice["id"]

    world.stripe.pay(session)
    assert world.checkout_event(COMPLETED, session).json() == {"status": "accepted"}
    world.run()
    doc = world.get_invoice(invoice["id"])
    assert (doc["status"], doc["balance_minor"]) == ("paid", 0)
    assert [(p["amount_minor"], p["method"]) for p in doc["payments"]] == [(6300, "card")]

    # Stripe sends more events for the same session (and may resend): the money is recorded once.
    world.checkout_event(COMPLETED, session)
    world.checkout_event(ASYNC_OK, session)
    world.run()
    assert len(world.get_invoice(invoice["id"])["payments"]) == 1
    requests = _summary(world, invoice)["requests"]
    assert [(r["status"], r["applied_minor"], r["surplus_minor"]) for r in requests] == [("paid", 6300, 0)]
    assert world.visitor.get(f"{PAY}/{token}").json()["state"] == "paid"
    r = world.visitor.post(f"{PAY}/{token}/checkout")
    assert (r.status_code, r.json()["code"]) == (409, "payments.invoice_settled")


def test_pay_links_need_active_stripe_and_an_issued_invoice(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    invoice = world.invoice()
    summary = _summary(world, invoice)
    assert (summary["available"], summary["unavailable_reason"], summary["link_url"]) == (False, "not_connected", None)
    r = world.owner.post(f"{API}/invoices/{invoice['id']}/link")
    assert (r.status_code, r.json()["code"]) == (409, "payments.not_connected")
    world.onboard()
    assert _summary(world, invoice)["unavailable_reason"] == "not_active"
    world.stripe.complete_onboarding(world.account_id())
    world.stripe_event()
    world.run()
    assert _summary(world, invoice)["available"] is True
    draft = world.invoice(issue=False)
    assert _summary(world, draft)["unavailable_reason"] == "not_issued"
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    assert _summary(world, invoice)["unavailable_reason"] == "regulated_profile"


def test_forged_and_replaced_links_stop_working(world: World) -> None:
    world.ready()
    invoice = world.invoice()
    token = _link(world, invoice)
    encoded, signature = token.split(".")
    for bad in (f"{encoded}.{signature[:-2]}AA", "not-a-token", f"{encoded}x.{signature}"):
        r = world.visitor.get(f"{PAY}/{bad}")
        assert (r.status_code, r.json()["code"]) == (404, "payments.link_not_found"), bad
    r = world.owner.post(f"{API}/invoices/{invoice['id']}/link/reset")
    fresh = r.json()["url"].rsplit("/", 1)[1]
    assert fresh != token
    assert world.visitor.get(f"{PAY}/{token}").status_code == 404
    assert world.visitor.post(f"{PAY}/{token}/checkout").status_code == 404
    assert world.visitor.get(f"{PAY}/{fresh}").json()["state"] == "payable"


def test_who_may_share_pay_links(world: World) -> None:
    world.ready()
    invoice = world.invoice()
    operator = login_as(world.app, world.business, ["operator"])
    assert operator.post(f"{API}/invoices/{invoice['id']}/link").status_code == 200
    for role in ("auditor", "inventory_manager", "marketing_manager"):
        client = login_as(world.app, world.business, [role])
        assert client.post(f"{API}/invoices/{invoice['id']}/link").status_code == 403, role
        assert client.get(f"{API}/invoices/{invoice['id']}").status_code == 403, role


def test_an_open_page_is_reused_and_closed_when_the_balance_changes(world: World) -> None:
    world.ready()
    invoice = world.invoice(6300)
    token = _link(world, invoice)
    first = _checkout(world, token)
    assert _checkout(world, token) == first
    assert len(world.stripe.sessions) == 1

    _cash(world, invoice, 1000)  # paid part in the shop: the open Stripe page for 63.00 is closed
    world.run()
    assert world.stripe.sessions[first]["status"] == "expired"
    assert [r["status"] for r in _summary(world, invoice)["requests"]] == ["expired"]

    second = _checkout(world, token)
    assert second != first and world.stripe.sessions[second]["amount_total"] == 5300


def test_money_taken_after_the_invoice_was_settled_is_kept_as_surplus(world: World) -> None:
    world.ready()
    invoice = world.invoice(6300)
    session = _checkout(world, _link(world, invoice))
    world.stripe.pay(session)  # the customer pays online ...
    _cash(world, invoice, 6300)  # ... while the shop records cash before Stripe's event arrives
    world.run()  # closing the page finds it paid, and applies it
    world.checkout_event(COMPLETED, session)
    world.run()
    doc = world.get_invoice(invoice["id"])
    assert (doc["status"], [p["method"] for p in doc["payments"]]) == ("paid", ["cash"])
    request = _summary(world, invoice)["requests"][0]
    assert (request["status"], request["applied_minor"], request["surplus_minor"]) == ("paid", 0, 6300)


def test_bank_debit_is_pending_until_the_bank_confirms(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "payments_bank_transfers", True)
    world.ready()
    invoice = world.invoice(6300)
    token = _link(world, invoice)
    session = _checkout(world, token)
    assert world.stripe.sessions[session]["payment_method_types"] == ["card", "us_bank_account"]

    world.stripe.start_bank_payment(session)
    world.checkout_event(COMPLETED, session)
    world.run()
    assert world.get_invoice(invoice["id"])["balance_minor"] == 6300  # nothing on the invoice yet
    assert world.visitor.get(f"{PAY}/{token}").json()["state"] == "processing"
    r = world.visitor.post(f"{PAY}/{token}/checkout")
    assert (r.status_code, r.json()["code"]) == (409, "payments.payment_processing")

    world.checkout_event(ASYNC_FAILED, session)
    world.run()
    assert _summary(world, invoice)["requests"][0]["status"] == "failed"
    assert world.visitor.get(f"{PAY}/{token}").json()["state"] == "payable"

    retry = _checkout(world, token)
    world.stripe.start_bank_payment(retry)
    world.checkout_event(COMPLETED, retry)
    world.run()
    world.stripe.finish_bank_payment(retry, ok=True)
    world.checkout_event(ASYNC_OK, retry)
    world.run()
    doc = world.get_invoice(invoice["id"])
    assert (doc["status"], [p["method"] for p in doc["payments"]]) == ("paid", ["bank_transfer"])


def test_the_hourly_check_catches_a_lost_webhook(world: World) -> None:
    world.ready()
    invoice = world.invoice(6300)
    session = _checkout(world, _link(world, invoice))
    world.stripe.pay(session)  # Stripe's event never arrives
    paylinks.check_job(_ctx(world), {})
    assert world.get_invoice(invoice["id"])["balance_minor"] == 6300  # too soon to look
    clock.set_clock(lambda: NOW + timedelta(minutes=20))
    paylinks.check_job(_ctx(world), {})
    assert world.get_invoice(invoice["id"])["status"] == "paid"


def test_expired_pages_and_abandoned_requests_are_closed(world: World) -> None:
    world.ready()
    invoice = world.invoice(6300)
    token = _link(world, invoice)
    session = _checkout(world, token)
    world.stripe.expire_session(session)
    world.checkout_event(EXPIRED, session)
    world.run()
    assert _summary(world, invoice)["requests"][0]["status"] == "expired"
    with transaction(_ctx(world)) as db:
        db.add(
            PaymentRequest(
                business_id=world.business,
                link_id=db.scalar(select(PaymentRequest.link_id)),
                invoice_id=invoice["id"],
                status="creating",
                amount_minor=6300,
                currency="USD",
                expires_at=NOW + timedelta(hours=1),
            )
        )
    clock.set_clock(lambda: NOW + timedelta(minutes=20))
    paylinks.check_job(_ctx(world), {})
    with transaction(_ctx(world)) as db:
        statuses = sorted(r.status for r in db.scalars(select(PaymentRequest)))
    assert statuses == ["expired", "failed"]


def test_relay_outage_leaves_the_invoice_payable(world: World) -> None:
    import httpx

    world.ready()
    invoice = world.invoice(6300)
    token = _link(world, invoice)
    world.stripe.fail_next.append(httpx.Response(503, json={"error": {"message": "down"}}))
    r = world.visitor.post(f"{PAY}/{token}/checkout")
    assert (r.status_code, r.json()["code"]) == (409, "payments.relay_unavailable")
    assert world.visitor.get(f"{PAY}/{token}").json()["state"] == "payable"
    assert _checkout(world, token)


def test_export_leaves_out_link_hashes_and_stripe_page_addresses(world: World) -> None:
    from app.modules.payments import lifecycle

    world.ready()
    invoice = world.invoice(6300)
    _checkout(world, _link(world, invoice))
    with transaction(_ctx(world)) as db:
        tables = lifecycle.export(db, _ctx(world))
        links, requests = list(tables["payment_links"]), list(tables["payment_requests"])
    assert len(links) == 1 and "token_hash" not in links[0]
    assert len(requests) == 1 and "checkout_url" not in requests[0] and requests[0]["amount_minor"] == 6300
