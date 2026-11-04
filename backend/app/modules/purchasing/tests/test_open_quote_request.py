"""The AI request guard is an ordinary tenant-scoped purchasing read."""

from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.org.testing import make_business
from app.modules.purchasing import public
from app.modules.purchasing.tests.test_quotes import context, request_quotes
from app.platform.db import transaction
from app.platform.errors import NotFound
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for


def test_find_open_quote_request(shop: Any, registry: Registry, owner_engine: Engine) -> None:
    ctx = context(shop)
    with transaction(ctx) as db:
        assert public.find_open_quote_request(db, ctx, shop["item"]) is None
    request_id = request_quotes(shop, registry)
    with transaction(ctx) as db:
        assert public.find_open_quote_request(db, ctx, shop["item"]) == request_id
    foreign = ctx_for(make_business(slug="example-foreign"), ["purchasing.po.read", "catalog.item.read"])
    with transaction(foreign) as db, pytest.raises(NotFound):
        public.find_open_quote_request(db, foreign, shop["item"])
    with owner_engine.begin() as db:
        db.execute(text("update quote_requests set status='closed' where id=:i"), {"i": request_id})
    with transaction(ctx) as db:
        assert public.find_open_quote_request(db, ctx, shop["item"]) is None
