from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.relay import signing
from app.relay.config import RelaySettings
from app.relay.main import create_app
from app.relay.testing import STRIPE_KEY, WEBHOOK_SECRET, FakeStripe

NOW = 1795000000
TANDEM = "https://shop.example.com"
EVENTS_URL = f"{TANDEM}/api/v1/payments/relay/events"
# Where deliveries actually connect: the address the resolver checked (tests resolve every name to it).
PUBLIC_IP = "93.184.216.34"
PINNED_EVENTS_URL = f"https://{PUBLIC_IP}/api/v1/payments/relay/events"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> int:
        return self.now


class Caller:
    """A Tandem business talking to the relay with its client id and secret."""

    def __init__(self, client: TestClient, client_id: str, secret: str, clock: Clock) -> None:
        self.client, self.client_id, self.secret, self.clock = client, client_id, secret, clock

    def call(self, method: str, path: str, body: dict[str, Any] | None = None, **over: Any) -> httpx.Response:
        content = httpx.Request("POST", "http://x", json=body).content if body is not None else b""
        nonce = over.pop("nonce", secrets.token_urlsafe(16))
        payload = signing.request_payload(method, path, nonce, content)
        headers = {
            signing.CLIENT_HEADER: over.pop("client_id", self.client_id),
            signing.NONCE_HEADER: nonce,
            signing.SIGNATURE_HEADER: signing.sign(over.pop("secret", self.secret), self.clock(), payload),
            "Content-Type": "application/json",
        }
        return self.client.request(method, path, content=content, headers=headers)


def settings_for(tmp_path: Path, **over: Any) -> RelaySettings:
    values: dict[str, Any] = {
        "secret_key": SecretStr("relay-test-secret-key-0123456789abcdef"),
        "database_url": f"sqlite:///{tmp_path / 'relay.db'}",
        "stripe_secret_key": SecretStr(STRIPE_KEY),
        "stripe_webhook_secret": SecretStr(WEBHOOK_SECRET),
        "deliver_interval_seconds": 0,
        **over,
    }
    return RelaySettings(**values)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def stripe(router: respx.MockRouter) -> FakeStripe:
    return FakeStripe(router)


@pytest.fixture
def make_relay(tmp_path: Path, clock: Clock, stripe: FakeStripe) -> Any:
    def make(**over: Any) -> TestClient:
        app = create_app(
            settings_for(tmp_path, **over),
            stripe_http=httpx.Client,
            callback_http=httpx.Client,
            resolver=lambda host: [PUBLIC_IP],
            clock=clock,
        )
        return TestClient(app)

    return make


@pytest.fixture
def relay(make_relay: Any) -> TestClient:
    return make_relay()  # type: ignore[no-any-return]


def register(relay: TestClient, clock: Clock, ref: str = "biz-1", **over: Any) -> Caller:
    body = {"name": "Example Garage", "business_ref": ref, "base_url": TANDEM, "events_url": EVENTS_URL, **over}
    r = relay.post("/v1/clients", json=body)
    assert r.status_code == 201, r.text
    return Caller(relay, r.json()["client_id"], r.json()["secret"], clock)
