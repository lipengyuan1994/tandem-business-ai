"""Relay Checkout Sessions: direct charges on the caller's own account, return paths, fee, bank debits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.relay.testing import FakeStripe

from .conftest import NOW, TANDEM, Clock, register

ACCOUNT = {"country": "US", "email": "owner@example.com", "name": "Example Garage"}
REQUEST = "11111111-2222-4333-8444-555555555555"
INVOICE = "66666666-7777-4888-8999-aaaaaaaaaaaa"
TOKEN = "pay1.abcdefghijklmnop.signature"


def checkout(**over: Any) -> dict[str, Any]:
    return {
        "request_id": REQUEST,
        "invoice_ref": INVOICE,
        "amount": 12_500,
        "currency": "usd",
        "description": "Invoice INV-0007",
        "success_path": f"/pay/{TOKEN}?result=done",
        "cancel_path": f"/pay/{TOKEN}",
        "expires_at": NOW + 3600,
        **over,
    }


def ready(relay: TestClient, clock: Clock, stripe: FakeStripe, ref: str = "biz-1") -> Any:
    caller = register(relay, clock, ref)
    account = caller.call("POST", "/v1/account", ACCOUNT).json()["id"]
    stripe.complete_onboarding(account)
    return caller, account


def test_checkout_is_a_direct_charge_returning_to_the_pay_page(
    relay: TestClient, clock: Clock, stripe: FakeStripe
) -> None:
    caller, account = ready(relay, clock, stripe)
    r = caller.call("POST", "/v1/checkout_sessions", checkout())
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["status"] == "open" and view["url"].startswith("https://checkout.stripe.com/")
    assert view["amount_total"] == 12_500 and view["currency"] == "usd"
    assert view["metadata"] == {"tandem_request": REQUEST, "tandem_invoice": INVOICE, "tandem_business_ref": "biz-1"}
    stored = stripe.sessions[view["id"]]
    assert stored["account"] == account
    assert stored["success_url"] == f"{TANDEM}/pay/{TOKEN}?result=done"
    assert stored["payment_method_types"] == ["card"]
    assert stored["application_fee_amount"] is None
    create = next(c for c in stripe.calls if c[:2] == ("POST", "/v1/checkout/sessions"))
    assert create[3]["stripe-account"] == account
    assert create[3]["idempotency-key"] == f"tandem-checkout-{caller.client_id}-{REQUEST}"
    # Retrying the same request returns the same session.
    assert caller.call("POST", "/v1/checkout_sessions", checkout()).json()["id"] == view["id"]
    assert len(stripe.sessions) == 1


def test_checkout_refuses_foreign_return_urls_and_bad_expiry(
    relay: TestClient, clock: Clock, stripe: FakeStripe
) -> None:
    caller, _ = ready(relay, clock, stripe)
    for path in ("https://evil.example/x", "//evil.example/pay/abcdefghijkl", "/settings/payments", "/pay/short"):
        r = caller.call("POST", "/v1/checkout_sessions", checkout(success_path=path))
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_return_path", path
    for expires in (NOW + 60, NOW + 2 * 86400):
        r = caller.call("POST", "/v1/checkout_sessions", checkout(expires_at=expires))
        assert r.json()["error"]["code"] == "invalid_expiry"
    assert not stripe.sessions


def test_bank_debits_only_when_asked_and_in_usd(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    caller, _ = ready(relay, clock, stripe)
    usd = caller.call("POST", "/v1/checkout_sessions", checkout(bank_transfers=True)).json()
    assert stripe.sessions[usd["id"]]["payment_method_types"] == ["card", "us_bank_account"]
    cad = caller.call(
        "POST",
        "/v1/checkout_sessions",
        checkout(bank_transfers=True, currency="cad", request_id="11111111-2222-4333-8444-000000000001"),
    ).json()
    assert stripe.sessions[cad["id"]]["payment_method_types"] == ["card"]


def test_application_fee_only_when_configured(
    make_relay: Any, tmp_path: Path, clock: Clock, stripe: FakeStripe
) -> None:
    relay = make_relay(application_fee_bps=100)
    caller, _ = ready(relay, clock, stripe)
    view = caller.call("POST", "/v1/checkout_sessions", checkout()).json()
    assert stripe.sessions[view["id"]]["application_fee_amount"] == "125"


def test_sessions_are_read_and_expired_only_on_the_callers_account(
    relay: TestClient, clock: Clock, stripe: FakeStripe
) -> None:
    a, _ = ready(relay, clock, stripe, "biz-a")
    b, _ = ready(relay, clock, stripe, "biz-b")
    session = a.call("POST", "/v1/checkout_sessions", checkout()).json()["id"]
    assert b.call("GET", f"/v1/checkout_sessions/{session}").status_code == 404
    assert b.call("POST", f"/v1/checkout_sessions/{session}/expire").status_code == 404
    assert a.call("GET", "/v1/checkout_sessions/not-a-session").status_code == 404
    stripe.pay(session)
    paid = a.call("GET", f"/v1/checkout_sessions/{session}").json()
    assert paid["status"] == "complete" and paid["payment_status"] == "paid"
    assert paid["payment_intent"].startswith("pi_") and paid["url"] is None
    refused = a.call("POST", f"/v1/checkout_sessions/{session}/expire")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "stripe_checkout_session_not_open"


def test_expire_closes_an_open_session(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    caller, _ = ready(relay, clock, stripe)
    session = caller.call("POST", "/v1/checkout_sessions", checkout()).json()["id"]
    r = caller.call("POST", f"/v1/checkout_sessions/{session}/expire")
    assert r.status_code == 200 and r.json()["status"] == "expired"


def test_checkout_needs_an_account_that_can_take_payments(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    caller = register(relay, clock)
    assert caller.call("POST", "/v1/checkout_sessions", checkout()).json()["error"]["code"] == "no_account"
    caller.call("POST", "/v1/account", ACCOUNT)
    r = caller.call("POST", "/v1/checkout_sessions", checkout())
    assert r.status_code == 422 and r.json()["error"]["code"] == "stripe_account_invalid"
