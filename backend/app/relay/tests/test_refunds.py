"""Relay refunds and disputes (phase 3): only on the caller's own account, keyed by Tandem's refund id."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.relay.testing import FakeStripe

from .conftest import Clock
from .test_checkout import INVOICE, checkout, ready

REFUND = "abcdefab-1111-4222-8333-444444444444"


def paid(caller: Any, stripe: FakeStripe) -> str:
    session = caller.call("POST", "/v1/checkout_sessions", checkout()).json()["id"]
    return str(stripe.pay(session)["payment_intent"])


def refund(intent: str, amount: int = 2_500, ref: str = REFUND) -> dict[str, Any]:
    return {"refund_ref": ref, "invoice_ref": INVOICE, "payment_intent": intent, "amount": amount}


def test_refund_is_keyed_by_tandems_refund_id(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    caller, account = ready(relay, clock, stripe)
    intent = paid(caller, stripe)
    r = caller.call("POST", "/v1/refunds", refund(intent))
    assert r.status_code == 200, r.text
    view = r.json()
    assert (view["amount"], view["status"], view["payment_intent"]) == (2_500, "succeeded", intent)
    assert view["metadata"] == {"tandem_refund": REFUND, "tandem_invoice": INVOICE, "tandem_business_ref": "biz-1"}
    create = next(c for c in stripe.calls if c[:2] == ("POST", "/v1/refunds"))
    assert create[3]["stripe-account"] == account
    assert create[3]["idempotency-key"] == f"tandem-refund-{caller.client_id}-{REFUND}"
    assert create[2]["reason"] == "requested_by_customer" and "refund_application_fee" not in create[2]
    # A retry after a lost answer gets the same refund back, never a second one.
    assert caller.call("POST", "/v1/refunds", refund(intent)).json()["id"] == view["id"]
    assert len(stripe.refunds) == 1
    assert caller.call("GET", f"/v1/refunds/{view['id']}").json() == view
    # More than what is left is Stripe's to refuse.
    too_much = caller.call("POST", "/v1/refunds", refund(intent, 12_500, "abcdefab-1111-4222-8333-555555555555"))
    assert too_much.status_code == 422 and too_much.json()["error"]["code"] == "stripe_amount_too_large"


def test_platform_fee_is_refunded_with_the_payment_when_there_is_one(
    make_relay: Any, clock: Clock, stripe: FakeStripe
) -> None:
    relay = make_relay(application_fee_bps=100)
    caller, _ = ready(relay, clock, stripe)
    intent = paid(caller, stripe)
    caller.call("POST", "/v1/refunds", refund(intent))
    assert next(iter(stripe.refunds.values()))["refund_application_fee"] == "true"


def test_refunds_and_disputes_only_on_the_callers_account(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    a, _ = ready(relay, clock, stripe, "biz-a")
    b, _ = ready(relay, clock, stripe, "biz-b")
    intent = paid(a, stripe)
    # B cannot refund A's payment: Stripe looks the payment up on B's own account and finds nothing.
    stolen = b.call("POST", "/v1/refunds", refund(intent))
    assert stolen.status_code == 422 and stolen.json()["error"]["code"] == "stripe_resource_missing"
    assert not stripe.refunds
    made = a.call("POST", "/v1/refunds", refund(intent)).json()["id"]
    dispute = stripe.dispute(intent)
    assert b.call("GET", f"/v1/refunds/{made}").status_code == 404
    assert b.call("GET", f"/v1/disputes/{dispute}").status_code == 404
    for path in ("/v1/refunds/ch_123", "/v1/refunds/..%2Fv1%2Faccounts", "/v1/disputes/re_123"):
        assert a.call("GET", path).status_code == 404, path
    view = a.call("GET", f"/v1/disputes/{dispute}").json()
    assert view == {
        "id": dispute,
        "amount": 10_000,
        "currency": "usd",
        "status": "needs_response",
        "reason": "fraudulent",
        "payment_intent": intent,
        "charge": f"ch_{intent[3:]}",
        "created": 1795000200,
        "due_by": 1795800000,
    }
