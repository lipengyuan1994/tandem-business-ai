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
from app.platform import clock, http
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

    def __init__(self, app: Any, business: Any, relay: TestClient, stripe: FakeStripe) -> None:
        self.app, self.business, self.relay, self.stripe = app, business, relay, stripe
        self.owner = login_as(app, business, ["owner"])

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
    return World(app, make_business(), relay, stripe)
