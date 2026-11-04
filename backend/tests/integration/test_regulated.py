"""P8-23: profile configuration, retained records, audited reads and UI-facing contracts."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.admin_ops.tests.test_data import CYCLE, NOW, customer
from app.modules.admin_ops.tests.test_data import demo as demo
from app.modules.identity.testing import login_as
from app.platform import clock
from app.platform.config import get_settings


@pytest.fixture
def regulated(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "data_profile", "regulated")
    monkeypatch.setattr(settings, "regulated_host_baa", "Example host, BAA signed 2027-01-10")
    monkeypatch.setattr(settings, "regulated_backup_baa", "Example backups, BAA signed 2027-01-10")


@pytest.mark.parametrize("profile", ["standard", "regulated"])
def test_system_and_public_profile(demo: Any, monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", profile)
    monkeypatch.setattr(get_settings(), "regulated_host_baa", "Example host")
    monkeypatch.setattr(get_settings(), "regulated_backup_baa", "Example backups")
    status = demo["owner"].get("/api/v1/admin/system").json()
    assert status["data_profile"] == profile
    assert status.get("regulated_host_baa") == ("Example host" if profile == "regulated" else None)
    public = demo["owner"].get("/api/v1/public/example-cycle-works/profile").json()
    assert public["avoid_medical_details"] is (profile == "regulated")
    assert "data_profile" not in public and "regulated_host_baa" not in public


@pytest.mark.parametrize("profile", ["standard", "regulated"])
def test_customer_reads_are_audited_once_per_user_target_ten_minutes(
    demo: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", profile)
    owner = demo["owner"]
    ada = customer(owner, "Ada Example")
    base = f"/api/v1/crm/customers/{ada['id']}"
    for suffix in ("", "/notes", "/timeline", "/consents", ""):
        assert owner.get(base + suffix).status_code == 200
    other = login_as(demo["app"], CYCLE, ["admin"])
    assert other.get(base).status_code == 200
    clock.set_clock(lambda: NOW + timedelta(minutes=10))
    assert owner.get(base).status_code == 200
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("select summary, changes, target_id from audit_events where action='crm.customer.viewed'")
        ).all()
    assert len(rows) == (3 if profile == "regulated" else 0)
    assert all(str(row.target_id) == ada["id"] and row.changes == {} and "Ada" not in row.summary for row in rows)


def test_restriction_retains_records_and_blocks_contact(
    demo: Any, regulated: None, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = demo["owner"]
    ben = customer(owner, "Ben Example")  # Upcoming appointments must be resolved before a retention hold.
    cid = ben["id"]
    assert (
        owner.post(f"/api/v1/crm/customers/{cid}/notes", json={"body": "Retain this personal note"}).status_code == 201
    )
    conv = owner.post(
        "/api/v1/conversations", json={"channel": "phone_log", "customer_id": cid, "body": "Retain conversation text"}
    )
    assert conv.status_code == 201, conv.text
    conv_id = conv.json()["id"]
    queued = owner.post(
        f"/api/v1/conversations/{conv_id}/messages", json={"direction": "outbound", "body": "Retain queued text"}
    )
    assert queued.status_code == 201, queued.text
    assert (
        owner.post(
            f"/api/v1/crm/customers/{cid}/contact-points", json={"type": "email", "value": "ben-secondary@example.com"}
        ).status_code
        == 201
    )
    suppressed = owner.post("/api/v1/marketing/suppressions", json={"value": "ben@example.com", "reason": "manual"})
    assert suppressed.status_code == 201
    with owner_engine.begin() as conn:
        conn.execute(text("update contact_points set is_active=false where value='ben-secondary@example.com'"))
        conn.execute(text("update appointments set notes='Retain appointment note' where customer_id=:c"), {"c": cid})
        before = {
            table: conn.execute(
                text(f"select count(*) from {table}")  # noqa: S608
            ).scalar()
            for table in ("customer_notes", "timeline_entries", "messages", "audit_events")
        }
    wrong = owner.post("/api/v1/admin/erasures", json={"customer_id": cid, "confirm_name": "wrong"})
    assert wrong.status_code == 422
    blocked = owner.post("/api/v1/admin/erasures", json={"customer_id": cid, "confirm_name": "Ben Example"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "admin_ops.erasure_blocked"
    with owner_engine.connect() as conn:
        upcoming = conn.execute(
            text(
                "select id, version from appointments where customer_id=:c "
                "and status in ('held','confirmed') and ends_at>:now"
            ),
            {"c": cid, "now": NOW},
        ).all()
    for appointment in upcoming:
        assert (
            owner.post(
                f"/api/v1/scheduling/appointments/{appointment.id}/cancel",
                json={"version": appointment.version, "reason": "Resolved before restriction"},
            ).status_code
            == 200
        )
    response = owner.post("/api/v1/admin/erasures", json={"customer_id": cid, "confirm_name": "Ben Example"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "restricted"
    again = owner.post("/api/v1/admin/erasures", json={"customer_id": cid, "confirm_name": "anything"})
    assert again.json()["id"] == response.json()["id"]
    for status in ("active", "restricted", "erased"):
        assert not owner.get("/api/v1/crm/customers", params={"status": status, "q": "Ben"}).json()["items"]
    assert owner.get(f"/api/v1/crm/customers/{cid}").status_code == 404
    matches = owner.get(
        "/api/v1/crm/customers/match", params={"name": "Ben Example", "email": "ben@example.com"}
    ).json()
    assert all(candidate["customer_id"] != cid for candidate in matches)
    assert (
        owner.post(
            f"/api/v1/conversations/{conv_id}/messages", json={"direction": "outbound", "body": "Blocked"}
        ).status_code
        == 409
    )
    from uuid import UUID

    from app.modules.crm import public as crm
    from app.platform.context import Context
    from app.platform.db import transaction

    ctx = Context(request_id="t", actor_kind="user", user_id=owner.user_id, business_id=CYCLE)
    with transaction(ctx) as db:
        assert crm.list_marketing_eligible(db, ctx, "email", [UUID(cid)]) == []
        assert all(not c.is_active for c in crm.audience_candidates(db, ctx, "email", [UUID(cid)]))
    with owner_engine.connect() as conn:
        appointment = conn.execute(
            text("select service_id, resource_id, starts_at from appointments where customer_id=:c limit 1"), {"c": cid}
        ).one()
    booked = owner.post(
        "/api/v1/scheduling/appointments",
        json={
            "customer_id": cid,
            "service_id": str(appointment.service_id),
            "resource_id": str(appointment.resource_id),
            "starts_at": (appointment.starts_at + timedelta(days=1)).isoformat(),
        },
    )
    assert booked.status_code == 404, booked.text
    # The cancelled appointment and its notes are retained; cancelling does not erase the record.
    with owner_engine.connect() as conn:
        held = conn.execute(
            text("select id, version, starts_at from appointments where customer_id=:c limit 1"), {"c": cid}
        ).one()
    moved = owner.post(
        f"/api/v1/scheduling/appointments/{held.id}/reschedule",
        json={
            "version": held.version,
            "starts_at": (held.starts_at + timedelta(days=1)).isoformat(),
        },
    )
    assert moved.status_code == 409 and moved.json()["code"] == "scheduling.not_active", moved.text
    with owner_engine.connect() as conn:
        assert conn.execute(text("select status from customers where id=:c"), {"c": cid}).scalar_one() == "restricted"
        contacts = conn.execute(
            text("select value, is_active from contact_points where customer_id=:c"), {"c": cid}
        ).all()
        assert contacts and all(value == "[erased]" and not active for value, active in contacts)
        assert (
            conn.execute(text("select reason from suppressions where value_normalized='ben@example.com'")).scalar_one()
            == "erased"
        )
        for table, count in before.items():
            assert (
                conn.execute(
                    text(f"select count(*) from {table}")  # noqa: S608
                ).scalar()
                >= count
            )
        assert (
            conn.execute(text("select body from customer_notes where customer_id=:c"), {"c": cid}).scalar_one()
            == "Retain this personal note"
        )
        assert (
            conn.execute(
                text("select count(*) from appointments where customer_id=:c and notes='Retain appointment note'"),
                {"c": cid},
            ).scalar_one()
            > 0
        )
        assert (
            conn.execute(text("select count(*) from messages where body='Retain conversation text'")).scalar_one() == 1
        )
        assert (
            conn.execute(
                text("select reason from suppressions where value_normalized='ben-secondary@example.com'")
            ).scalar_one()
            == "erased"
        )
        assert (
            str(conn.execute(text("select id from suppressions where value_normalized='ben@example.com'")).scalar_one())
            == suppressed.json()["id"]
        )
        pending = conn.execute(
            text("select id, variables, status from outbox_messages where related_id=:m"), {"m": queued.json()["id"]}
        ).one()
        assert pending.status == "failed" and pending.variables == {"body": "Retain queued text"}
    from app.modules.notifications.service import deliver

    assert deliver(CYCLE, pending.id) == "skipped"
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    assert (
        owner.post("/api/v1/admin/erasures", json={"customer_id": cid, "confirm_name": "anything"}).json()["status"]
        == "restricted"
    )


@pytest.mark.parametrize("profile", ["standard", "regulated"])
def test_conversation_and_appointment_reads(
    demo: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", profile)
    owner = demo["owner"]
    with owner_engine.connect() as conn:
        conversation_id = str(
            conn.execute(text("select id from conversations where business_id=:b limit 1"), {"b": CYCLE}).scalar_one()
        )
        appointment_id = str(
            conn.execute(text("select id from appointments where business_id=:b limit 1"), {"b": CYCLE}).scalar_one()
        )
    for _ in range(2):
        assert owner.get(f"/api/v1/conversations/{conversation_id}").status_code == 200
        assert owner.get(f"/api/v1/scheduling/appointments/{appointment_id}").status_code == 200
    denied = login_as(demo["app"], CYCLE, ["marketing_manager"])
    assert denied.get(f"/api/v1/scheduling/appointments/{appointment_id}").status_code == 403
    with owner_engine.connect() as conn:
        actions = (
            conn.execute(text("select action from audit_events where action like '%.viewed' order by action"))
            .scalars()
            .all()
        )
    assert actions == (
        ["conversations.conversation.viewed", "scheduling.appointment.viewed"] if profile == "regulated" else []
    )


@pytest.mark.parametrize("profile,idle,hours", [("standard", 60, 12), ("regulated", 15, 8)])
def test_staff_session_idle_and_absolute_limits(
    demo: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, profile: str, idle: int, hours: int
) -> None:
    from app.modules.identity.sessions import create_session, is_live
    from app.platform.context import Context
    from app.platform.db import transaction

    monkeypatch.setattr(get_settings(), "data_profile", profile)
    ctx = Context(request_id="t", actor_kind="user", user_id=demo["owner"].user_id, business_id=CYCLE)
    with transaction(ctx) as db:
        row, _, _ = create_session(db, ctx.user_id, CYCLE)
        assert row.expires_at == NOW + timedelta(hours=hours)
        assert is_live(row)
        clock.set_clock(lambda: NOW + timedelta(minutes=idle) - timedelta(seconds=1))
        assert is_live(row)
        clock.set_clock(lambda: NOW + timedelta(minutes=idle))
        assert not is_live(row)
        row.last_seen_at = NOW + timedelta(hours=hours)
        clock.set_clock(lambda: NOW + timedelta(hours=hours) - timedelta(seconds=1))
        assert is_live(row)
        clock.set_clock(lambda: NOW + timedelta(hours=hours))
        assert not is_live(row)


def test_switching_to_regulated_caps_existing_sessions(
    demo: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    with owner_engine.begin() as conn:
        conn.execute(
            text("update sessions set created_at=:old, authenticated_at=:old, last_seen_at=:now, expires_at=:future"),
            {"old": NOW - timedelta(hours=8), "now": NOW, "future": NOW + timedelta(hours=4)},
        )
    assert demo["owner"].get("/api/v1/auth/me").status_code == 401


def test_concurrent_view_reads_write_one_event(demo: Any, regulated: None, owner_engine: Engine) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from uuid import UUID

    from app.platform.audit import record_view
    from app.platform.context import Context
    from app.platform.db import transaction

    owner = demo["owner"]
    ada = customer(owner, "Ada Example")
    ctx = Context(request_id="parallel-views", actor_kind="user", user_id=owner.user_id, business_id=CYCLE)
    barrier = threading.Barrier(2)

    def view() -> None:
        barrier.wait()
        with transaction(ctx) as db:
            record_view(db, ctx, action="crm.customer.viewed", target_type="customer", target_id=UUID(ada["id"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(view) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("select count(*) from audit_events where action='crm.customer.viewed'")).scalar_one() == 1
        )


def test_restriction_suppression_promotion_cannot_cross_businesses(demo: Any) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.modules.admin_ops.tests.test_data import GARDEN
    from app.platform.context import Context
    from app.platform.db import transaction

    ctx = Context(request_id="t", actor_kind="user", user_id=demo["owner"].user_id, business_id=CYCLE)
    with pytest.raises(DBAPIError), transaction(ctx) as db:
        db.execute(text("select restrict_mark_erased_suppression(:b, :v)"), {"b": GARDEN, "v": "gwen@example.com"})
