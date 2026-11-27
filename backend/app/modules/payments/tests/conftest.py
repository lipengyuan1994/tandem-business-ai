from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.modules.payments import provider
from app.platform import clock, events, http, jobs
from app.platform.config import get_settings
from app.platform.registry import Registry
from app.relay.config import RelaySettings
from app.relay.main import create_app as create_relay
from app.relay.testing import STRIPE_KEY, WEBHOOK_SECRET, FakeStripe, asgi_route

NOW = datetime(2026, 11, 27, 15, tzinfo=UTC)
RELAY_HOST = "relay.example.com"
API = "/api/v1/payments"


class World:
    """One business on a Tandem install, the relay, and Stripe (fake), wired together over respx."""

    def __init__(self, app: Any, business: Any, relay: TestClient, stripe: FakeStripe, registry: Registry) -> None:
        self.app, self.business, self.relay, self.stripe, self.registry = app, business, relay, stripe, registry
        self.owner = login_as(app, business, ["owner"])
        self.visitor = TestClient(app, base_url="https://localhost:8443")

    def ready(self) -> None:
        """Set up Stripe and finish onboarding, so the business can take payments."""
        assert self.onboard().status_code == 200
        self.stripe.complete_onboarding(self.account_id())
        assert self.stripe_event().json() == {"status": "accepted"}
        self.run()
        assert self.status()["status"] == "active"

    def run(self) -> None:
        """Run what the worker would: event deliveries to subscribers and queued jobs, until both are idle."""
        for _ in range(10):
            if not (events.deliver_pending(self.registry) + jobs.run_pending(self.registry)):
                return

    def invoice(self, total: int = 6300, *, issue: bool = True) -> dict[str, Any]:
        r = self.owner.post("/api/v1/crm/customers", json={"display_name": "Ada Example"})
        assert r.status_code == 201, r.text
        customer = r.json()["id"]
        r = self.owner.post(
            "/api/v1/catalog/items", json={"name": "Wiper blades", "kind": "product", "price_minor": total}
        )
        assert r.status_code == 201, r.text
        item = r.json()["id"]
        r = self.owner.post(
            "/api/v1/sales/invoices",
            json={"customer_id": customer, "lines": [{"catalog_item_id": item, "quantity": "1"}]},
        )
        assert r.status_code == 201, r.text
        doc = dict(r.json())
        if issue:
            r = self.owner.post(f"/api/v1/sales/invoices/{doc['id']}/issue", json={"version": doc["version"]})
            assert r.status_code in (200, 201), r.text
            doc = dict(r.json())
        return doc

    def get_invoice(self, invoice_id: str) -> dict[str, Any]:
        r = self.owner.get(f"/api/v1/sales/invoices/{invoice_id}")
        assert r.status_code == 200, r.text
        return dict(r.json())

    def checkout_event(self, kind: str, session_id: str, **over: Any) -> httpx.Response:
        event = self.stripe.event(self.account_id(), kind, {"id": session_id, "object": "checkout.session"}, **over)
        body, header = self.stripe.signed(event, int(clock.now().timestamp()))
        return self.relay.post("/v1/stripe/webhook", content=body, headers={"Stripe-Signature": header})

    def onboard(self, country: str = "US", client: TestClient | None = None) -> httpx.Response:
        return (client or self.owner).post(f"{API}/stripe/onboarding", json={"country": country})

    def status(self) -> dict[str, Any]:
        r = self.owner.get(f"{API}/stripe")
        assert r.status_code == 200, r.text
        return dict(r.json())

    def account_id(self) -> str:
        return str(self.status()["stripe_account_id"])

    def stripe_event(self, kind: str = "account.updated", **over: Any) -> httpx.Response:
        account = over.pop("account", self.account_id())
        event = self.stripe.event(account, kind, {"id": account, "object": "account"}, **over)
        body, header = self.stripe.signed(event, int(clock.now().timestamp()))
        return self.relay.post("/v1/stripe/webhook", content=body, headers={"Stripe-Signature": header})


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "payments_relay_url", f"https://{RELAY_HOST}")
    monkeypatch.setattr(settings, "payments_relay_enrollment_token", SecretStr(""))
    monkeypatch.setattr(provider, "http_client", lambda: http.client(allowlist=[RELAY_HOST], resolve=False))


@pytest.fixture
def world(registry: Registry, router: respx.MockRouter, configured: None, tmp_path: Path) -> World:
    clock.set_clock(lambda: NOW)
    stripe = FakeStripe(router)
    relay_settings = RelaySettings(
        secret_key=SecretStr("relay-test-secret-key-0123456789abcdef"),
        database_url=f"sqlite:///{tmp_path / 'relay.db'}",
        stripe_secret_key=SecretStr(STRIPE_KEY),
        stripe_webhook_secret=SecretStr(WEBHOOK_SECRET),
        deliver_interval_seconds=0,
    )
    relay = TestClient(
        create_relay(
            relay_settings,
            stripe_http=httpx.Client,
            callback_http=httpx.Client,
            resolver=lambda host: ["93.184.216.34"],
            clock=lambda: int(clock.now().timestamp()),
        )
    )
    app = create_app(registry)
    asgi_route(router, RELAY_HOST, relay)
    asgi_route(router, "localhost", TestClient(app, base_url="https://localhost:8443"))
    return World(app, make_business(), relay, stripe, registry)
