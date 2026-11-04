from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform.db import transaction
from app.platform.errors import Conflict, NotFound
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for

from .. import public, service
from ..schemas import DraftLineIn


def test_draft_public_read_tenant_version_and_single_conversion(registry: Registry) -> None:
    business = make_business()
    c = login_as(create_app(registry), business, ["owner"])
    item = c.post(
        "/api/v1/catalog/items", json={"kind": "product", "name": "Example tube", "is_stock_tracked": True}
    ).json()["id"]
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        draft = service.create_draft(db, ctx, [DraftLineIn(item_id=uuid.UUID(item), quantity=Decimal(3))], "Example")
        draft_id = draft.id
        read = public.read_reorder_draft(db, ctx, draft_id)
        assert read.version == 1 and read.lines[0].quantity == 3
    with pytest.raises(NotFound):
        with transaction(ctx_for(make_business(slug="other-example"))) as db:
            public.read_reorder_draft(db, ctx_for(uuid.uuid4()), draft_id)
    with pytest.raises(Conflict):
        with transaction(ctx) as db:
            public.mark_reorder_draft_converted(db, ctx, draft_id, 2)
    with transaction(ctx) as db:
        public.mark_reorder_draft_converted(db, ctx, draft_id, 1)
        assert public.read_reorder_draft(db, ctx, draft_id).status == "sent_to_purchasing"
    with pytest.raises(Conflict):
        with transaction(ctx) as db:
            public.mark_reorder_draft_converted(db, ctx, draft_id, 2)
