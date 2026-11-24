from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx
from pydantic import SecretStr

from app.main import create_app
from app.modules.accounting import provider
from app.modules.accounting.quickbooks import INTUIT_HOSTS
from app.modules.org.testing import make_business
from app.platform import clock, http
from app.platform.config import get_settings
from app.platform.registry import Registry

from .fake_quickbooks import FakeQuickBooks
from .helpers import NOW, Shop, connect, map_accounts


@pytest.fixture
def intuit(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeQuickBooks]:
    settings = get_settings()
    monkeypatch.setattr(settings, "quickbooks_client_id", "example-client")
    monkeypatch.setattr(settings, "quickbooks_client_secret", SecretStr("example-secret"))
    monkeypatch.setattr(settings, "quickbooks_environment", "sandbox")
    monkeypatch.setattr(provider, "http_client", lambda: http.client(allowlist=INTUIT_HOSTS, resolve=False))
    with respx.mock(assert_all_called=False) as router:
        yield FakeQuickBooks(router)


@pytest.fixture
def shop(registry: Registry) -> Shop:
    clock.set_clock(lambda: NOW)
    return Shop(create_app(registry), make_business())


@pytest.fixture
def books(shop: Shop, intuit: FakeQuickBooks, registry: Registry) -> Any:
    """A shop connected to the fake QuickBooks company with the required accounts mapped."""
    connect(shop)
    map_accounts(shop)
    return shop
