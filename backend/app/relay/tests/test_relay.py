"""Relay API: registration, signed calls, one account per business, onboarding links, webhook forwarding."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from app.relay import signing
from app.relay.models import RelayEvent
from app.relay.testing import FakeStripe

from .conftest import EVENTS_URL, PINNED_EVENTS_URL, TANDEM, Clock, register

ACCOUNT = {"country": "US", "email": "owner@example.com", "name": "Example Garage"}


def test_health_reports_mode_without_secrets(relay: TestClient) -> None:
    r = relay.get("/healthz")
    assert r.json() == {"ok": True, "mode": "test", "stripe_configured": True}


def test_register_returns_secret_once_and_stores_it_sealed(relay: TestClient, clock: Clock) -> None:
    caller = register(relay, clock)
    assert caller.client_id.startswith("rc_") and len(caller.secret) >= 40
    db = relay.app.state.relay.db  # type: ignore[attr-defined]
    with db.engine.connect() as conn:
        stored = conn.exec_driver_sql("select secret_sealed from relay_clients").scalar_one()
    assert caller.secret not in stored


def test_enrollment_token_is_required_when_configured(make_relay: Any, clock: Clock) -> None:
    relay = make_relay(enrollment_token="enroll-example-token")
    body = {"name": "Example", "business_ref": "b", "base_url": TANDEM, "events_url": EVENTS_URL}
    assert relay.post("/v1/clients", json=body).status_code == 401
    r = relay.post("/v1/clients", json=body, headers={"Authorization": "Bearer enroll-example-token"})
    assert r.status_code == 201


def test_registration_is_rate_limited(make_relay: Any) -> None:
    relay = make_relay(enrollments_per_hour=2)
    body = {"name": "Example", "business_ref": "b", "base_url": TANDEM, "events_url": EVENTS_URL}
    assert [relay.post("/v1/clients", json=body).status_code for _ in range(3)] == [201, 201, 429]


def test_events_url_must_be_public_https(make_relay: Any) -> None:
    relay = make_relay()
    base = {"name": "Example", "business_ref": "b", "base_url": TANDEM}
    for url in ("http://shop.example.com/x", "https://10.0.0.5/x", "https://user:pw@shop.example.com/x"):
        r = relay.post("/v1/clients", json={**base, "events_url": url})
        assert r.status_code == 422, url
    private = make_relay(private_callback_hosts="api")
    r = private.post("/v1/clients", json={**base, "events_url": "http://api:8000/api/v1/payments/relay/events"})
    assert r.status_code == 201


def test_events_url_resolving_to_private_address_is_refused(tmp_path: Any, clock: Clock, stripe: FakeStripe) -> None:
    from app.relay.main import create_app

    from .conftest import settings_for

    app = create_app(settings_for(tmp_path), resolver=lambda host: ["127.0.0.1"], clock=clock)
    r = TestClient(app).post(
        "/v1/clients", json={"name": "E", "business_ref": "b", "base_url": TANDEM, "events_url": EVENTS_URL}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_events_url"


def test_signed_calls_refuse_bad_signature_stale_time_and_replay(relay: TestClient, clock: Clock) -> None:
    caller = register(relay, clock)
    assert caller.call("GET", "/v1/account", secret="not-the-secret").status_code == 401
    assert caller.call("GET", "/v1/account", client_id="rc_unknown").status_code == 401
    r = relay.get("/v1/account")
    assert r.status_code == 401
    nonce = "fixed-nonce-0123456789"
    assert caller.call("GET", "/v1/account", nonce=nonce).status_code == 404  # authenticated, no account yet
    replay = caller.call("GET", "/v1/account", nonce=nonce)
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "replayed"
    clock.now += signing.TOLERANCE_SECONDS + 1
    stale_sig = signing.sign(
        caller.secret, clock.now - 400, signing.request_payload("GET", "/v1/account", "n" * 20, b"")
    )
    r = relay.get(
        "/v1/account",
        headers={
            signing.CLIENT_HEADER: caller.client_id,
            signing.NONCE_HEADER: "n" * 20,
            signing.SIGNATURE_HEADER: stale_sig,
        },
    )
    assert r.status_code == 401


def test_create_account_uses_default_controller_settings_once(
    relay: TestClient, clock: Clock, stripe: FakeStripe
) -> None:
    caller = register(relay, clock)
    first = caller.call("POST", "/v1/account", ACCOUNT)
    assert first.status_code == 200, first.text
    account = first.json()
    assert account["charges_enabled"] is False and "external_account" in account["currently_due"]
    stored = stripe.accounts[account["id"]]
    assert stored["controller"] == {
        "fees": {"payer": "account"},
        "losses": {"payments": "stripe"},
        "stripe_dashboard": {"type": "full"},
        "requirement_collection": "stripe",
    }
    assert stored["metadata"] == {"tandem_relay_client": caller.client_id, "tandem_business_ref": "biz-1"}
    again = caller.call("POST", "/v1/account", ACCOUNT)
    assert again.json()["id"] == account["id"]
    assert len(stripe.accounts) == 1
    creates = [c for c in stripe.calls if c[:2] == ("POST", "/v1/accounts")]
    assert creates[0][3]["idempotency-key"] == f"tandem-relay-account-{caller.client_id}"


def test_each_client_reaches_only_its_own_account(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    a = register(relay, clock, "biz-a")
    b = register(relay, clock, "biz-b")
    account_a = a.call("POST", "/v1/account", ACCOUNT).json()["id"]
    assert b.call("GET", "/v1/account").status_code == 404
    assert b.call("POST", "/v1/account/onboarding_link").status_code == 404
    account_b = b.call("POST", "/v1/account", ACCOUNT).json()["id"]
    assert account_a != account_b
    assert a.call("GET", "/v1/account").json()["id"] == account_a


def test_onboarding_link_returns_to_the_settings_page(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    caller = register(relay, clock)
    account = caller.call("POST", "/v1/account", ACCOUNT).json()["id"]
    r = caller.call("POST", "/v1/account/onboarding_link")
    assert r.status_code == 200 and r.json()["url"].startswith("https://connect.stripe.com/")
    link = stripe.links[-1]
    assert link["account"] == account and link["type"] == "account_onboarding"
    assert link["return_url"] == f"{TANDEM}/settings/payments?stripe=return"
    assert link["refresh_url"] == f"{TANDEM}/settings/payments?stripe=refresh"


def test_stripe_failures_map_to_clear_errors(make_relay: Any, clock: Clock, stripe: FakeStripe) -> None:
    relay = make_relay()
    caller = register(relay, clock)
    stripe.fail_next.append(httpx.Response(500, json={"error": {"message": "boom"}}))
    r = caller.call("POST", "/v1/account", ACCOUNT)
    assert r.status_code == 503 and r.json()["error"]["code"] == "stripe_unavailable"
    stripe.fail_next.append(httpx.Response(400, json={"error": {"code": "country_unsupported", "message": "No"}}))
    r = caller.call("POST", "/v1/account", ACCOUNT)
    assert r.status_code == 422 and r.json()["error"]["code"] == "stripe_country_unsupported"
    keyless = make_relay(stripe_secret_key="")
    caller = register(keyless, clock)
    assert caller.call("POST", "/v1/account", ACCOUNT).json()["error"]["code"] == "stripe_not_configured"


def _webhook(
    relay: TestClient, stripe: FakeStripe, event: dict[str, Any], now: int, secret: str | None = None
) -> httpx.Response:
    body, header = stripe.signed(event, now) if secret is None else stripe.signed(event, now, secret)
    return relay.post("/v1/stripe/webhook", content=body, headers={"Stripe-Signature": header})


def test_webhook_checks_stripe_signature(relay: TestClient, clock: Clock, stripe: FakeStripe) -> None:
    event = stripe.event("acct_x", "account.updated", {"id": "acct_x", "object": "account"})
    assert _webhook(relay, stripe, event, clock.now, secret="whsec_wrong").status_code == 400
    assert _webhook(relay, stripe, event, clock.now - 600).status_code == 400
    assert _webhook(relay, stripe, event, clock.now).json() == {"status": "ignored"}  # unknown account


def test_webhook_forwards_envelope_signed_once(
    relay: TestClient, clock: Clock, stripe: FakeStripe, router: respx.MockRouter
) -> None:
    caller = register(relay, clock)
    account = caller.call("POST", "/v1/account", ACCOUNT).json()["id"]
    received: list[httpx.Request] = []
    router.post(PINNED_EVENTS_URL).mock(side_effect=lambda req: received.append(req) or httpx.Response(200, json={}))
    event = stripe.event(account, "account.updated", {"id": account, "object": "account", "email": "x@example.com"})
    assert _webhook(relay, stripe, event, clock.now).json() == {"status": "accepted"}
    assert _webhook(relay, stripe, event, clock.now).json() == {"status": "duplicate"}
    assert len(received) == 1
    request = received[0]
    body = json.loads(request.content)
    assert body == {
        "id": event["id"],
        "type": "account.updated",
        "account": account,
        "business_ref": "biz-1",
        "created": 1795000000,
        "livemode": False,
        "object": {"id": account, "object": "account"},
    }
    assert "x@example.com" not in request.content.decode()
    # Posted to the address that was checked, never a fresh DNS answer; Host and TLS name stay the real ones.
    assert request.headers["host"] == "shop.example.com" and request.extensions["sni_hostname"] == "shop.example.com"
    assert request.headers[signing.CLIENT_HEADER] == caller.client_id
    assert signing.verify(caller.secret, request.headers[signing.SIGNATURE_HEADER], request.content, clock.now)


def test_delivery_rechecks_the_address_and_never_posts_to_a_private_one(
    tmp_path: Any, clock: Clock, stripe: FakeStripe, router: respx.MockRouter
) -> None:
    from app.relay.main import create_app

    from .conftest import settings_for

    asked: list[str] = []

    def resolver(host: str) -> list[str]:  # public when registered, private by the time it is posted
        asked.append(host)
        return ["93.184.216.34"] if len(asked) == 1 else ["10.0.0.5"]

    app = create_app(
        settings_for(tmp_path), stripe_http=httpx.Client, callback_http=httpx.Client, resolver=resolver, clock=clock
    )
    relay = TestClient(app)
    caller = register(relay, clock)
    account = caller.call("POST", "/v1/account", ACCOUNT).json()["id"]
    route = router.route(host="10.0.0.5").mock(return_value=httpx.Response(200))
    event = stripe.event(account, "account.updated", {"id": account, "object": "account"})
    _webhook(relay, stripe, event, clock.now)
    assert route.call_count == 0
    with app.state.relay.db.session() as db:  # type: ignore[attr-defined]
        assert db.get(RelayEvent, event["id"]).last_error == "shop.example.com resolves to a non-public address"


def test_failed_delivery_backs_off_and_gives_up_after_three_days(
    relay: TestClient, clock: Clock, stripe: FakeStripe, router: respx.MockRouter
) -> None:
    caller = register(relay, clock)
    account = caller.call("POST", "/v1/account", ACCOUNT).json()["id"]
    route = router.post(PINNED_EVENTS_URL).mock(return_value=httpx.Response(503))
    event = stripe.event(account, "account.updated", {"id": account, "object": "account"})
    _webhook(relay, stripe, event, clock.now)
    state = relay.app.state.relay  # type: ignore[attr-defined]
    assert state.deliver() == 0  # not due yet: next attempt in a minute
    clock.now += 61
    assert state.deliver() == 0 and route.call_count == 2
    with state.db.session() as db:
        row = db.get(RelayEvent, event["id"])
        assert row.attempts == 2 and row.next_attempt_at == clock.now + 300 and row.last_error == "answered 503"
    route.mock(return_value=httpx.Response(204))
    clock.now += 300
    assert state.deliver() == 1
    second = stripe.event(account, "account.updated", {"id": account, "object": "account"})
    route.mock(return_value=httpx.Response(500))
    _webhook(relay, stripe, second, clock.now)
    clock.now += 3 * 86400
    state.deliver()
    with state.db.session() as db:
        assert db.get(RelayEvent, second["id"]).given_up_at == clock.now
