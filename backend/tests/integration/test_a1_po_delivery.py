"""A1: PO delivery is a local atomic transition plus one stoppable outbox row."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.modules.approvals import engine
from app.modules.approvals import service as approvals
from app.modules.identity import public as identity
from app.modules.identity.testing import add_member, make_user
from app.modules.notifications.models import OutboxMessage
from app.modules.notifications.templates import render, render_html
from app.modules.purchasing import service
from app.modules.purchasing.schemas import POCreate, POLineIn
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.registry import get_registry
from tests.integration.test_a1_chat_confirm import shop as shop


def owner(shop: Any) -> Context:
    user = make_user()
    add_member(user, shop["business"], ["owner"])
    return Context("test", "user", user, shop["business"], identity.ROLES["owner"])


def approved(shop: Any, ctx: Context, *, cost: int = 1200) -> Any:
    with transaction(ctx) as db:
        po = service.create(
            db,
            ctx,
            POCreate(
                supplier_id=shop["supplier"],
                notes="INTERNAL CANARY",
                lines=[POLineIn(catalog_item_id=shop["item"], quantity="10", unit_cost_minor=cost)],
            ),
        )
        return service.transition(db, ctx, po.id, po.version, "approve")


def test_send_po_queues_supplier_once_and_marks_sent(shop: Any) -> None:
    ctx = owner(shop)
    po = approved(shop, ctx)
    with transaction(ctx) as db:
        proposal = approvals.create_proposal(
            db,
            ctx,
            action_type="purchasing.send_po",
            title="Send PO",
            payload={"purchase_order_id": str(po.id), "version": po.version},
        )
    other = owner(shop)
    with transaction(other) as db:
        rev = approvals.get_detail(db, other, proposal.id)["current_revision"]
        approvals.decide(
            db, other, proposal.id, decision="approve", revision_id=rev.id, payload_hash=rev.payload_hash, comment=None
        )
    assert engine.execute(ctx.business_id, proposal.id, proposal.current_revision_id) == "succeeded"
    assert engine.execute(ctx.business_id, proposal.id, proposal.current_revision_id) == "done"
    with transaction(ctx) as db:
        m = db.scalars(select(OutboxMessage).where(OutboxMessage.proposal_id == proposal.id)).one()
        assert m.recipient == "parts@example.com" and m.channel == "simulated"
        assert m.not_before is None  # staff-origin proposal
        assert service.repo.order(db, ctx, po.id).status == "sent"
        subject, body = render(m.template_key, m.variables)
        assert po.number in subject and "Example cable" in body and "120.00 USD" in body
        assert "INTERNAL CANARY" not in body and "INTERNAL CANARY" not in str(m.variables)
        assert "Delivery location:" in body
        html = render_html(m.template_key, {**m.variables, "business_name": "<script>"})
        assert "&lt;script&gt;" in html and "<script>" not in html
        with pytest.raises(DomainError) as exc:
            approvals.create_proposal(
                db,
                ctx,
                action_type="purchasing.send_po",
                title="Duplicate",
                payload={"purchase_order_id": str(po.id), "version": po.version + 1},
            )
        assert exc.value.code == "purchasing.po_already_sent"


def test_send_po_missing_supplier_email_is_clear(shop: Any) -> None:
    ctx = owner(shop)
    po = approved(shop, ctx)
    with transaction(ctx) as db:
        service.repo.supplier(db, ctx, po.supplier_id).email = None
    with transaction(ctx) as db, pytest.raises(DomainError) as exc:
        approvals.create_proposal(
            db,
            ctx,
            action_type="purchasing.send_po",
            title="Send",
            payload={"purchase_order_id": str(po.id), "version": po.version},
        )
    assert exc.value.code == "purchasing.supplier_email_required"


@pytest.mark.parametrize("case", ["large", "currency", "new"])
def test_send_po_second_person_metadata(shop: Any, case: str) -> None:
    ctx = owner(shop)
    po = approved(shop, ctx, cost=5001 if case == "large" else 1200)
    with transaction(ctx) as db:
        if case == "currency":
            service.repo.order(db, ctx, po.id).currency = "EUR"
        spec = get_registry().executors["purchasing.send_po"]
        payload = spec.payload_model.model_validate({"purchase_order_id": po.id, "version": po.version})
        assert callable(spec.second_person) and spec.second_person(db, ctx, payload)


def test_po_mail_reply_to_and_rendering_without_external_send(shop: Any, monkeypatch: Any) -> None:
    from datetime import timedelta

    from app.modules.notifications import service as notifications
    from app.modules.notifications.adapters import Delivered
    from app.modules.org.models import Business
    from app.platform import clock
    from tests.integration.test_a1_chat_confirm import confirm, order, proposal

    order(shop, sent=True)
    with transaction(shop["ctx"]) as db:
        business = db.get(Business, shop["business"])
        business.public_profile = {**business.public_profile, "email": "business@example.com"}
    # Exercise the email rendering path with a local in-memory adapter, never SMTP/network.
    monkeypatch.setattr("app.modules.purchasing.delivery.notifications.email_channel", lambda: "email")
    captured = []

    class Capture:
        def send(self, message, key):
            captured.append(message)
            return Delivered("synthetic")

    monkeypatch.setitem(notifications.ADAPTERS, "email", Capture())
    p = proposal(shop, order(shop))
    assert confirm(shop, p).status_code == 200
    assert engine.execute(shop["business"], p["id"], p["revision_id"]) == "succeeded"
    with transaction(shop["ctx"]) as db:
        ident = db.scalars(select(OutboxMessage.id)).one()
    now = clock.now()
    clock.set_clock(lambda: now + timedelta(seconds=120))
    assert notifications.deliver(shop["business"], ident) == "delivered"
    assert len(captured) == 1
    assert captured[0].reply_to == "business@example.com" and captured[0].recipient == "parts@example.com"
    assert "Example cable" in captured[0].body and "Example cable" in captured[0].html


def test_supplier_deactivation_before_execution_keeps_po_and_mail_atomic(shop: Any) -> None:
    from tests.integration.test_a1_chat_confirm import confirm, order, proposal

    order(shop, sent=True)
    po = order(shop)
    p = proposal(shop, po)
    assert confirm(shop, p).status_code == 200
    with transaction(shop["ctx"]) as db:
        service.repo.supplier(db, shop["ctx"], po.supplier_id).active = False
    assert engine.execute(shop["business"], p["id"], p["revision_id"]) == "failed"
    with transaction(shop["ctx"]) as db:
        assert service.repo.order(db, shop["ctx"], po.id).status == "approved"
        assert list(db.scalars(select(OutboxMessage))) == []
        assert approvals.get_detail(db, shop["ctx"], p["id"])["proposal"].failure_code == "purchasing.supplier_inactive"
