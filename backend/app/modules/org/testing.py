"""Test helper: a fully bootstrapped business (default location + settings) through org's public API."""

from __future__ import annotations

import uuid

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import new_id

from .public import create_business


def make_business(
    slug: str = "example-cycle-works", timezone: str = "America/Denver", currency: str = "USD"
) -> uuid.UUID:
    business_id = new_id()
    ctx = Context(request_id="test", actor_kind="system", user_id=None, business_id=business_id)
    with transaction(ctx) as db:
        create_business(
            db,
            ctx,
            name=slug.replace("-", " ").title(),
            slug=slug,
            timezone=timezone,
            currency=currency,
            locale="en-US",
        )
    return business_id


def system_ctx(business_id: uuid.UUID, permissions: frozenset[str] = frozenset()) -> Context:
    return Context(
        request_id="test", actor_kind="system", user_id=None, business_id=business_id, permissions=permissions
    )
