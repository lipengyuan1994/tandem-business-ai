"""Settings > Payments: relay registration, the Stripe account, onboarding links and readiness (M21 phase 1)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.modules.identity.testing import login_as
from app.modules.payments import signing as tandem_signing
from app.modules.payments.models import PaymentConnection, PaymentEvent
from app.platform import jobs
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.registry import Registry
from app.relay import signing as relay_signing

from .conftest import API, World


def _ctx(world: World) -> Context:
    return Context(request_id="t", actor_kind="system", user_id=None, business_id=world.business)


def _connection(world: World) -> PaymentConnection:
    with transaction(_ctx(world)) as db:
        row = db.scalar(select(PaymentConnection))
        assert row is not None
        db.expunge(row)
        return row


def test_signing_schemes_agree() -> None:
    payload = relay_signing.request_payload("POST", "/v1/account", "nonce-0123456789ab", b'{"a":1}')
    assert payload == tandem_signing.request_payload("POST", "/v1/account", "nonce-0123456789ab", b'{"a":1}')
    header = relay_signing.sign("secret", 1795000000, payload)
    assert header == tandem_signing.sign("secret", 1795000000, payload)
    assert tandem_signing.verify("secret", header, payload, 1795000100)
    assert not tandem_signing.verify("secret", header, payload, 1795000301)
    assert not tandem_signing.verify("other", header, payload, 1795000000)


def test_not_offered_without_a_relay(registry: Registry) -> None:
    from app.main import create_app
    from app.modules.org.testing import make_business

    app = create_app(registry)
    owner = login_as(app, make_business(), ["owner"])
    r = owner.get(f"{API}/stripe")
    assert r.json()["configured"] is False and r.json()["status"] == "not_connected"
    r = owner.post(f"{API}/stripe/onboarding", json={"country": "US"})
    assert (r.status_code, r.json()["code"]) == (409, "payments.not_configured")


def test_onboarding_registers_creates_the_account_and_returns_a_stripe_link(world: World) -> None:
    r = world.onboard()
    assert r.status_code == 200, r.text
    assert r.json()["url"].startswith("https://connect.stripe.com/setup/s/")
    status = world.status()
    assert status["status"] == "onboarding" and status["mode"] == "test"
    assert status["stripe_account_id"].startswith("acct_test")
    assert "external_account" in status["requirements_due"]
    assert status["dashboard_url"] == "https://dashboard.stripe.com/test"
    account = world.stripe.accounts[status["stripe_account_id"]]
    assert account["controller"]["stripe_dashboard"] == {"type": "full"}
    assert account["metadata"]["tandem_business_ref"] == str(world.business)
    assert world.stripe.links[-1]["return_url"] == "https://localhost:8443/settings/payments?stripe=return"
    conn = _connection(world)
    assert conn.relay_client_id and conn.relay_secret_sealed and conn.connected_at is not None
    assert conn.relay_client_id not in (conn.relay_secret_sealed or "")


def test_each_click_makes_a_fresh_link_for_the_same_account(world: World) -> None:
    first = world.onboard().json()["url"]
    client_id = _connection(world).relay_client_id
    second = world.onboard().json()["url"]
    assert first != second
    assert len(world.stripe.accounts) == 1 and len(world.stripe.links) == 2
    assert _connection(world).relay_client_id == client_id


def test_account_event_through_the_relay_makes_payments_active(world: World, registry: Registry) -> None:
    world.onboard()
    world.stripe.complete_onboarding(world.account_id())
    r = world.stripe_event()
    assert r.json() == {"status": "accepted"}
    assert jobs.run_pending(registry) >= 1
    status = world.status()
    assert status["status"] == "active" and status["charges_enabled"] and status["payouts_enabled"]
    assert status["requirements_due"] == []
    with transaction(_ctx(world)) as db:
        row = db.scalar(select(PaymentEvent))
        assert row is not None and row.outcome == "applied" and row.event_type == "account.updated"


def test_events_are_stored_once(world: World, registry: Registry) -> None:
    world.onboard()
    relay_state = world.relay.app.state.relay  # type: ignore[attr-defined]
    event_id = "evt_example0001"
    world.stripe_event(event_id=event_id)
    # The relay retries a delivery whose answer it never saw: Tandem must not store it twice.
    with relay_state.db.session() as db:
        from app.relay.models import RelayEvent

        db.get(RelayEvent, event_id).delivered_at = None
    relay_state.deliver()
    jobs.run_pending(registry)
    with transaction(_ctx(world)) as db:
        assert len(db.scalars(select(PaymentEvent)).all()) == 1


def _post_event(world: World, body: dict[str, Any], client_id: str, secret: str, now: int) -> httpx.Response:
    raw = httpx.Request("POST", "http://x", json=body).content
    return world.owner.post(
        f"{API}/relay/events",
        content=raw,
        headers={
            "Content-Type": "application/json",
            tandem_signing.CLIENT_HEADER: client_id,
            tandem_signing.SIGNATURE_HEADER: tandem_signing.sign(secret, now, raw),
        },
    )


def test_forged_events_are_refused(world: World) -> None:
    world.onboard()
    conn = _connection(world)
    body = {
        "id": "evt_forged0001",
        "type": "account.updated",
        "account": conn.stripe_account_id,
        "business_ref": str(world.business),
        "object": {"id": conn.stripe_account_id, "object": "account"},
    }
    now = 1795000000
    from app.platform import clock

    now = int(clock.now().timestamp())
    assert _post_event(world, body, conn.relay_client_id or "", "guessed-secret", now).status_code == 401
    assert _post_event(world, body, "rc_other", "guessed-secret", now).status_code == 401
    from app.modules.org.testing import make_business

    other = {**body, "business_ref": str(make_business(slug="other-shop"))}
    assert _post_event(world, other, conn.relay_client_id or "", "guessed-secret", now).status_code == 401
    bad = {**body, "id": "not-an-event"}
    assert _post_event(world, bad, conn.relay_client_id or "", "guessed-secret", now).status_code == 422


def test_refresh_shows_what_stripe_still_needs(world: World) -> None:
    world.onboard()
    world.stripe.restrict(world.account_id(), ["individual.verification.document"])
    r = world.owner.post(f"{API}/stripe/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "restricted" and body["requirements_due"] == ["individual.verification.document"]
    assert body["disabled_reason"] == "requirements.past_due"


def test_disconnect_and_set_up_again(world: World, registry: Registry) -> None:
    world.onboard()
    world.stripe.complete_onboarding(world.account_id())
    version = world.owner.post(f"{API}/stripe/refresh").json()["version"]
    r = world.owner.post(f"{API}/stripe/disconnect", json={"version": version - 1})
    assert (r.status_code, r.json()["code"]) == (409, "payments.version_conflict")
    r = world.owner.post(f"{API}/stripe/disconnect", json={"version": version})
    assert r.status_code == 200 and r.json()["status"] == "disconnected"
    world.stripe_event()
    jobs.run_pending(registry)
    assert world.status()["status"] == "disconnected"  # an account event does not turn payments back on
    world.onboard()
    assert world.status()["status"] == "active"
    assert len(world.stripe.accounts) == 1


def test_only_owner_and_admin_manage(world: World) -> None:
    operator = login_as(world.app, world.business, ["operator"])
    assert operator.get(f"{API}/stripe").status_code == 403
    assert world.onboard(client=operator).status_code == 403
    admin = login_as(world.app, world.business, ["admin"])
    assert world.onboard(client=admin).status_code == 200
    reviewer = login_as(world.app, world.business, ["demo_reviewer"])
    assert reviewer.get(f"{API}/stripe").status_code == 200
    assert world.onboard(client=reviewer).status_code == 403


def test_refused_under_the_regulated_profile(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    r = world.onboard()
    assert (r.status_code, r.json()["code"]) == (409, "payments.regulated_profile")


def test_stripe_outage_is_reported_and_kept(world: World) -> None:
    world.onboard()
    world.stripe.fail_next.append(httpx.Response(500, json={"error": {"message": "Stripe is down"}}))
    r = world.onboard()
    assert (r.status_code, r.json()["code"]) == (409, "payments.relay_unavailable")
    assert world.status()["last_error"] == "Stripe is down"
    assert world.onboard().status_code == 200
    assert world.status()["last_error"] is None


def test_export_leaves_out_the_relay_secret(world: World) -> None:
    from app.modules.payments import lifecycle

    world.onboard()
    with transaction(_ctx(world)) as db:
        tables = lifecycle.export(db, _ctx(world))
    text = repr(tables)
    assert "relay_secret_sealed" not in text and "payment_connections" in text


def test_an_account_disconnected_in_stripe_stops_online_payments(world: World, registry: Registry) -> None:
    world.onboard()
    account = world.account_id()
    world.stripe.complete_onboarding(account)
    world.owner.post(f"{API}/stripe/refresh")
    # A deauthorization for another account, or one Stripe can still read, changes nothing.
    world.stripe_event("account.application.deauthorized")
    jobs.run_pending(registry)
    assert world.status()["status"] == "active"
    other = dict(world.stripe.accounts[account], id="acct_other")
    world.stripe.accounts["acct_other"] = other
    del world.stripe.accounts[account]  # Stripe refuses to read an account that left the platform
    world.stripe_event("account.application.deauthorized", account="acct_other")
    jobs.run_pending(registry)
    assert world.status()["status"] == "active"
    world.stripe_event("account.application.deauthorized")
    jobs.run_pending(registry)
    status = world.status()
    assert status["status"] == "disconnected" and status["last_error"]
    notes = world.owner.get("/api/v1/notifications").json()["items"]
    assert any("Stripe account was disconnected" in n["title"] for n in notes)
    with transaction(_ctx(world)) as db:
        outcomes = sorted(r.outcome for r in db.scalars(select(PaymentEvent)))
    assert outcomes.count("applied") >= 2


def test_mode_follows_the_relay_when_its_operator_sets_a_live_key(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.onboard()
    assert world.status()["mode"] == "test"
    relay_settings = world.relay.app.state.relay.settings
    monkeypatch.setattr(type(relay_settings), "mode", property(lambda self: "live"))
    r = world.owner.post(f"{API}/stripe/refresh")
    assert r.status_code == 200 and r.json()["mode"] == "live"
