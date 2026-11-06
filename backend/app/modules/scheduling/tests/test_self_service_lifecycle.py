"""P10-C2b private simulated-mail projection, export and erasure integration."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from .test_self_service import BASE, NOW, Shop, enable, logged_in, request_link
from .test_self_service import shop as shop


def test_simulated_email_permissions_filter_export_and_erasure(shop: Shop, owner_engine: Engine) -> None:
    from app.modules.identity.testing import login_as
    from app.modules.scheduling import lifecycle
    from app.platform.db import transaction
    from app.platform.registry import get_registry

    client, appt = logged_in(shop, owner_engine)
    _, token = request_link(shop, owner_engine)
    with owner_engine.begin() as db:
        db.execute(text("update outbox_messages set created_at=:now"), {"now": NOW})
    r = shop.owner.get("/api/v1/notifications/simulated-emails")
    assert r.status_code == 200 and token in r.text
    assert TestClient(shop.app).get("/api/v1/notifications/simulated-emails").status_code == 401
    assert login_as(shop.app, shop.business, []).get("/api/v1/notifications/simulated-emails").status_code == 403
    assert (
        login_as(shop.app, shop.business, ["inventory_manager"])
        .get("/api/v1/notifications/simulated-emails")
        .status_code
        == 403
    )
    assert login_as(shop.app, shop.business, ["admin"]).get("/api/v1/notifications/simulated-emails").status_code == 200
    with transaction(shop.ctx()) as db:
        exported = lifecycle.export(db, shop.ctx())
        assert {"self_service_tokens", "self_service_sessions", "self_service_changes"} <= exported.keys()
        assert "token_hash" not in json.dumps(exported)
        mail_export = get_registry().export_handlers["notifications"](db, shop.ctx())
        assert token not in json.dumps(mail_export)
    assert client.post(BASE + f"/bookings/{appt['id']}/cancel", json={"version": 1}).status_code == 200
    erased = shop.owner.post("/api/v1/admin/erasures", json={"customer_id": shop.ben, "confirm_name": "Ben Example"})
    assert erased.status_code == 200, erased.text
    assert client.get(BASE + "/bookings").status_code == 403
    with owner_engine.connect() as db:
        assert db.execute(text("select count(*) from self_service_tokens where revoked_at is null")).scalar_one() == 0
        assert db.execute(text("select fee_text from self_service_changes")).scalar_one() is None
    assert token not in shop.owner.get("/api/v1/notifications/simulated-emails").text


def test_opt_out_cancels_queued_transactional_mail(shop: Shop, owner_engine: Engine) -> None:
    client, _ = logged_in(shop, owner_engine)
    with owner_engine.begin() as db:
        db.execute(text("update customers set preferred_contact_channel='none' where id=:id"), {"id": shop.ben})
        assert (
            db.execute(
                text("select count(*) from outbox_messages where template_key='self_service_link' and status='pending'")
            ).scalar_one()
            == 0
        )
    assert client.get(BASE + "/bookings").status_code == 403


def test_simulated_view_excludes_real_old_and_other_tenant(shop: Shop, owner_engine: Engine) -> None:
    from app.modules.notifications import public as notifications
    from app.modules.org.testing import make_business
    from app.platform.context import system_context
    from app.platform.db import transaction

    enable(shop)
    other = make_business("example-garden-store")
    ctx = shop.ctx()
    with transaction(ctx) as db:
        old = notifications.enqueue_message(
            db,
            ctx,
            channel="simulated",
            recipient="old@example.com",
            template_key="test_message",
            variables={"name": "Old Example"},
            idempotency_key="old",
        )
        real = notifications.enqueue_message(
            db,
            ctx,
            channel="email",
            recipient="real@example.com",
            template_key="test_message",
            variables={"name": "Real Example"},
            idempotency_key="real",
        )
        recent = notifications.enqueue_message(
            db,
            ctx,
            channel="simulated",
            recipient="recent@example.com",
            template_key="test_message",
            variables={"name": "Recent Example"},
            idempotency_key="recent",
        )
    with transaction(system_context(other)) as db:
        other_mail = notifications.enqueue_message(
            db,
            system_context(other),
            channel="simulated",
            recipient="other@example.com",
            template_key="test_message",
            variables={"name": "Other Example"},
            idempotency_key="other",
        )
    with owner_engine.begin() as db:
        db.execute(text("update outbox_messages set created_at=:now"), {"now": NOW})
        db.execute(
            text("update outbox_messages set created_at=:old where id=:id"),
            {"old": NOW - timedelta(hours=25), "id": old},
        )
    r = shop.owner.get("/api/v1/notifications/simulated-emails")
    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [str(recent)]
    with owner_engine.begin() as db:
        db.execute(
            text("update outbox_messages set created_at=:future where id=:id"),
            {"future": NOW + timedelta(hours=1), "id": recent},
        )
    assert shop.owner.get("/api/v1/notifications/simulated-emails").json() == []
    assert str(real) not in r.text and str(other_mail) not in r.text
