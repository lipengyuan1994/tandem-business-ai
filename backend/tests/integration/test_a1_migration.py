"""A1 populated migration compatibility and safe downgrade boundaries."""

from __future__ import annotations

from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.modules.approvals import service as approvals
from app.platform.db import transaction
from app.platform.testing.plugin import TEST_DB_OWNER
from tests.integration.test_a1_chat_confirm import order, proposal
from tests.integration.test_a1_chat_confirm import shop as shop
from tests.integration.test_a1_send_delay import queued


def test_a1_upgrade_preserves_legacy_supplier_outbox_and_human_decision(shop: Any, owner_engine: Any) -> None:
    from app.modules.identity.testing import add_member, make_user
    from app.platform.context import Context

    ctx = shop["ctx"]
    user = make_user()
    add_member(user, shop["business"], ["owner"])
    other = Context("test", "user", user, shop["business"], ctx.permissions)
    with transaction(ctx) as db:
        p = approvals.create_proposal(
            db, ctx, action_type="testing.noop_write", title="Legacy approval", payload={"name": "legacy"}
        )
        rev = approvals.get_detail(db, ctx, p.id)["current_revision"]
    with transaction(other) as db:
        approvals.decide(
            db, other, p.id, decision="approve", revision_id=rev.id, payload_hash=rev.payload_hash, comment=None
        )
    from app.modules.notifications import public as notifications

    with transaction(ctx) as db:
        outbox_id = notifications.enqueue_message(
            db,
            ctx,
            channel="simulated",
            recipient="legacy@example.com",
            template_key="test_message",
            variables={"name": "Legacy"},
            idempotency_key="legacy",
        )
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB_OWNER
    try:
        command.downgrade(cfg, "20261105_0800_autonomy")
        with owner_engine.connect() as db:
            assert (
                db.scalar(text("select decided_by from approval_decisions where proposal_id=:p"), {"p": p.id}) == user
            )
        command.upgrade(cfg, "head")
        with owner_engine.connect() as db:
            row = db.execute(
                text("select decided_by,decided_by_kind,decided_via from approval_decisions where proposal_id=:p"),
                {"p": p.id},
            ).one()
            assert tuple(row) == (user, "user", "approvals")
            message = db.execute(
                text("select status,not_before from outbox_messages where id=:i"), {"i": outbox_id}
            ).one()
            assert tuple(message) == ("pending", None)
            assert db.scalar(text("select active from suppliers where id=:s"), {"s": shop["supplier"]}) is True
    finally:
        command.upgrade(cfg, "head")


def test_a1_downgrade_refuses_chat_provenance_loss(shop: Any) -> None:
    from tests.integration.test_a1_chat_confirm import confirm

    order(shop, sent=True)
    p = proposal(shop, order(shop))
    assert confirm(shop, p).status_code == 200
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB_OWNER
    with pytest.raises(RuntimeError, match="chat approval provenance"):
        command.downgrade(cfg, "20261105_0800_autonomy")


def test_a1_downgrade_refuses_stopped_delivery_loss(shop: Any) -> None:
    ident = queued(shop)
    assert shop["client"].post(f"/api/v1/notifications/outbox/{ident}/stop").status_code == 200
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB_OWNER
    with pytest.raises(RuntimeError, match="stopped delivery history"):
        command.downgrade(cfg, "20261105_0800_autonomy")
