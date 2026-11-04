from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import create_app
from app.modules.org import public
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.registry import Registry
from app.platform.testing.auth import client_for, ctx_for

ALL_ORG = {
    "org.business.read",
    "org.business.write",
    "org.location.read",
    "org.location.write",
    "org.settings.read",
    "org.settings.write",
}


def bootstrap(slug: str, timezone: str = "America/Denver") -> uuid.UUID:
    business_id = new_id()
    ctx = ctx_for(business_id)
    with transaction(ctx) as db:
        public.create_business(
            db, ctx, name=slug.replace("-", " ").title(), slug=slug, timezone=timezone, currency="USD", locale="en-US"
        )
    return business_id


@pytest.fixture
def cycle() -> uuid.UUID:
    return bootstrap("example-cycle-works")


@pytest.fixture
def garden() -> uuid.UUID:
    return bootstrap("example-garden-store", "Europe/London")


@pytest.fixture
def app_reg(registry: Registry) -> tuple[Any, Registry]:
    return create_app(registry), registry


@pytest.fixture
def client(app_reg: tuple[Any, Registry]) -> Any:
    app, reg = app_reg

    def _make(business_id: uuid.UUID, permissions: set[str] | None = None) -> Any:
        return client_for(app, reg, business_id, ALL_ORG if permissions is None else permissions)

    return _make
