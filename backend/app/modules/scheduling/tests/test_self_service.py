"""P10-C2b customer-owned booking access and mutation boundaries."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.modules.scheduling.tests.test_scheduling import NOW, Shop
from app.modules.scheduling.tests.test_scheduling import shop as shop
from app.platform import clock

SLUG = "example-cycle-works"
BASE = f"/api/v1/public/{SLUG}/self-service"
ORIGIN = {"Origin": "https://localhost:8443"}


def enable(shop: Shop, **changes: Any) -> None:
    current = shop.owner.get("/api/v1/org/settings").json()
    current["settings"].update(self_service_enabled=True, **changes)
    r = shop.owner.patch("/api/v1/org/settings", json=current)
    assert r.status_code == 200, r.text


def contact(shop: Shop) -> None:
    r = shop.owner.post(
        f"/api/v1/crm/customers/{shop.ben}/contact-points",
        json={"type": "email", "value": "ben@example.com", "is_primary": True},
    )
    assert r.status_code == 201, r.text


def booking(shop: Shop) -> dict[str, Any]:
    r = shop.book(shop.brake, shop.ben, "2026-11-10T17:00:00Z")
    assert r.status_code == 201, r.text
    return dict(r.json())


def request_link(shop: Shop, owner_engine: Engine) -> tuple[TestClient, str]:
    client = TestClient(shop.app, base_url="https://localhost:8443", headers=ORIGIN)
    r = client.post(BASE + "/request", json={"email": "ben@example.com"})
    assert r.status_code == 202 and "token" not in r.text and "#t=" not in r.text
    with owner_engine.connect() as db:
        variables = db.execute(
            text(
                "select variables from outbox_messages where template_key='self_service_link' "
                "order by created_at desc limit 1"
            )
        ).scalar_one()
    return client, str(variables["link"]).split("#t=")[1]


def logged_in(shop: Shop, owner_engine: Engine) -> tuple[TestClient, dict[str, Any]]:
    enable(shop)
    contact(shop)
    appt = booking(shop)
    client, token = request_link(shop, owner_engine)
    r = client.post(BASE + "/exchange", json={"token": token})
    assert r.status_code == 200, r.text
    return client, appt


def test_link_hash_single_use_scope_minimal_projection(shop: Shop, owner_engine: Engine) -> None:
    enable(shop)
    contact(shop)
    appt = booking(shop)
    client, token = request_link(shop, owner_engine)
    with owner_engine.connect() as db:
        rows = db.execute(text("select * from self_service_tokens")).mappings().all()
        assert token not in json.dumps([dict(r) for r in rows], default=str)
        assert len(rows[0]["token_hash"]) == 64
    result = client.post(BASE + "/exchange", json={"token": token})
    assert result.status_code == 200
    cookie = result.headers["set-cookie"]
    assert all(v in cookie for v in ["HttpOnly", "Secure", "SameSite=strict", f"Path={BASE}/", "Max-Age=3600"])
    assert client.post(BASE + "/exchange", json={"token": token}).status_code == 403
    rows = client.get(BASE + "/bookings").json()["items"]
    assert rows[0]["id"] == appt["id"]
    assert not {"notes", "customer_id", "resource_name", "price_minor", "blocked_until"} & rows[0].keys()
    assert (
        client.post(
            BASE + f"/bookings/{appt['id']}/cancel", json={"version": 1}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("hours,ack,status", [(0, False, 200), (200, False, 422), (200, True, 200)])
def test_cancel_notice_ack_notifications(shop: Shop, owner_engine: Engine, hours: int, ack: bool, status: int) -> None:
    client, appt = logged_in(shop, owner_engine)
    enable(shop, cancellation_notice_hours=hours, cancellation_fee_text="A $10 fee applies")
    r = client.post(
        BASE + f"/bookings/{appt['id']}/cancel",
        json={"version": 1, "fee_acknowledged": ack, "acknowledged_fee_text": "A $10 fee applies" if ack else None},
    )
    assert r.status_code == status, r.text
    if status == 200:
        with owner_engine.connect() as db:
            assert db.execute(text("select count(*) from self_service_changes")).scalar_one() == 1
            assert (
                db.execute(
                    text("select count(*) from outbox_messages where template_key='self_service_confirmation'")
                ).scalar_one()
                == 1
            )
            assert (
                db.execute(
                    text("select count(*) from in_app_notifications where kind='self_service_change'")
                ).scalar_one()
                >= 1
            )
            assert "by customer via self-service" in str(
                db.execute(
                    text("select summary from audit_events where action='scheduling.self_service.cancel'")
                ).scalar_one()
            )


def test_reschedule_shared_slots_and_stale_version(shop: Shop, owner_engine: Engine) -> None:
    client, appt = logged_in(shop, owner_engine)
    r = client.get(
        BASE + f"/bookings/{appt['id']}/slots",
        params={"earliest": "2026-11-11T16:00:00Z", "latest": "2026-11-11T23:00:00Z"},
    )
    assert r.status_code == 200, r.text
    slot = r.json()[0]
    body = {"version": 1, "starts_at": slot["starts_at"], "resource_id": slot["resource_id"]}
    r = client.post(BASE + f"/bookings/{appt['id']}/reschedule", json=body)
    assert r.status_code == 200, r.text
    assert client.post(BASE + f"/bookings/{appt['id']}/reschedule", json=body).status_code == 409
    assert client.get(BASE + "/bookings").json()["items"][0]["id"] == appt["id"]
    assert shop.available() == 2  # existing parts reservation retained


def test_expiry_and_idle_session(shop: Shop, owner_engine: Engine) -> None:
    enable(shop)
    contact(shop)
    booking(shop)
    client, token = request_link(shop, owner_engine)
    clock.set_clock(lambda: NOW + timedelta(minutes=21))
    assert client.post(BASE + "/exchange", json={"token": token}).status_code == 403
    client, token = request_link(shop, owner_engine)
    assert client.post(BASE + "/exchange", json={"token": token}).status_code == 200
    clock.set_clock(lambda: NOW + timedelta(minutes=82))
    assert client.get(BASE + "/bookings").status_code == 403


@pytest.mark.parametrize(
    "state", ["unknown", "erased", "restricted", "archived", "opted_out", "no_bookings", "disabled", "known"]
)
def test_neutral_response_eligibility_and_timing_class(
    shop: Shop, owner_engine: Engine, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.scheduling import self_service_api

    enable(shop)
    contact(shop)
    if state != "no_bookings":
        booking(shop)
    if state in {"erased", "restricted", "archived"}:
        with owner_engine.begin() as db:
            db.execute(text("update customers set status=:state where id=:id"), {"state": state, "id": shop.ben})
    if state == "opted_out":
        with owner_engine.begin() as db:
            db.execute(text("update customers set preferred_contact_channel='none' where id=:id"), {"id": shop.ben})
    if state == "disabled":
        current = shop.owner.get("/api/v1/org/settings").json()
        current["settings"]["self_service_enabled"] = False
        assert shop.owner.patch("/api/v1/org/settings", json=current).status_code == 200
    waits: list[float] = []
    from types import SimpleNamespace

    monkeypatch.setattr(self_service_api, "time", SimpleNamespace(monotonic=lambda: 10.0, sleep=waits.append))
    client = TestClient(shop.app, base_url="https://localhost:8443", headers=ORIGIN)
    r = client.post(
        BASE + "/request", json={"email": "unknown@example.com" if state == "unknown" else "ben@example.com"}
    )
    assert r.status_code == 202 and r.json() == {
        "message": "If eligible bookings match that address, we'll email a sign-in link."
    }
    assert waits == [0.25]
    with owner_engine.connect() as db:
        count = db.execute(text("select count(*) from self_service_tokens")).scalar_one()
    assert count == int(state == "known")


def test_public_rate_limits_ip_and_email(shop: Shop, owner_engine: Engine) -> None:
    enable(shop)
    client = TestClient(shop.app, base_url="https://localhost:8443", headers=ORIGIN)
    for _ in range(3):
        assert client.post(BASE + "/request", json={"email": "unknown@example.com"}).status_code == 202
    assert client.post(BASE + "/request", json={"email": "UNKNOWN@example.com"}).status_code == 429
    with owner_engine.begin() as db:
        db.execute(text("truncate rate_limits"))
    for i in range(10):
        assert client.post(BASE + "/request", json={"email": f"unknown{i}@example.com"}).status_code == 202
    assert client.post(BASE + "/request", json={"email": "another@example.com"}).status_code == 429


@pytest.mark.parametrize("state", ["erased", "restricted", "archived"])
def test_lifecycle_revokes_tokens_and_sessions_even_after_reactivation(
    shop: Shop, owner_engine: Engine, state: str
) -> None:
    client, _ = logged_in(shop, owner_engine)
    _, token = request_link(shop, owner_engine)
    with owner_engine.begin() as db:
        db.execute(text("update customers set status=:state where id=:id"), {"state": state, "id": shop.ben})
        assert db.execute(text("select count(*) from self_service_sessions where revoked_at is null")).scalar_one() == 0
        assert db.execute(text("select count(*) from self_service_tokens where revoked_at is null")).scalar_one() == 0
    assert client.get(BASE + "/bookings").status_code == 403
    with owner_engine.begin() as db:
        db.execute(text("update customers set status='active' where id=:id"), {"id": shop.ben})
    assert client.get(BASE + "/bookings").status_code == 403
    assert client.post(BASE + "/exchange", json={"token": token}).status_code == 403


def test_scope_other_customer_other_tenant_and_rls(shop: Shop, owner_engine: Engine) -> None:
    from app.modules.org.testing import make_business
    from app.platform.context import system_context
    from app.platform.db import transaction

    client, appt = logged_in(shop, owner_engine)
    other = shop.book(shop.tuneup, shop.ada, "2026-11-10T20:00:00Z").json()
    assert len(client.get(BASE + "/bookings").json()["items"]) == 1
    assert client.post(BASE + f"/bookings/{other['id']}/cancel", json={"version": 1}).status_code == 404
    assert (
        client.get(
            BASE + f"/bookings/{other['id']}/slots",
            params={"earliest": "2026-11-11T16:00:00Z", "latest": "2026-11-12T00:00:00Z"},
        ).status_code
        == 404
    )
    tenant = make_business("example-garden-store")
    from app.modules.identity.testing import login_as

    other_owner = login_as(shop.app, tenant, ["owner"])
    other_settings = other_owner.get("/api/v1/org/settings").json()
    other_settings["settings"]["self_service_enabled"] = True
    assert other_owner.patch("/api/v1/org/settings", json=other_settings).status_code == 200
    _, token = request_link(shop, owner_engine)
    assert (
        client.post("/api/v1/public/example-garden-store/self-service/exchange", json={"token": token}).status_code
        == 403
    )
    # Even a maliciously copied cookie cannot select the other business.
    cookie = next(c.value for c in client.cookies.jar if c.name == "smb_self_service")
    assert (
        client.get(
            "/api/v1/public/example-garden-store/self-service/bookings",
            headers={"Cookie": f"smb_self_service={cookie}"},
        ).status_code
        == 403
    )
    with transaction(system_context(tenant)) as db:
        for table in ("self_service_tokens", "self_service_sessions", "self_service_changes"):
            assert (
                db.execute(
                    text(f"select count(*) from {table}")  # noqa: S608 - fixed table tuple above
                ).scalar_one()
                == 0
            )
    assert appt["id"] != other["id"]


def test_staff_change_conflicts_and_changed_fee_cannot_be_acknowledged(shop: Shop, owner_engine: Engine) -> None:
    client, appt = logged_in(shop, owner_engine)
    enable(shop, cancellation_notice_hours=200, cancellation_fee_text="New fee")
    r = client.post(
        BASE + f"/bookings/{appt['id']}/cancel",
        json={"version": 1, "fee_acknowledged": True, "acknowledged_fee_text": "Old fee"},
    )
    assert r.status_code == 409
    staff = shop.owner.post(
        f"/api/v1/scheduling/appointments/{appt['id']}/reschedule",
        json={"version": 1, "starts_at": "2026-11-11T17:00:00Z"},
    )
    assert staff.status_code == 200, staff.text
    r = client.post(
        BASE + f"/bookings/{appt['id']}/reschedule",
        json={
            "version": 1,
            "starts_at": "2026-11-12T17:00:00Z",
            "resource_id": shop.jordan,
            "fee_acknowledged": True,
            "acknowledged_fee_text": "New fee",
        },
    )
    assert r.status_code == 409
    assert client.get(BASE + "/bookings").json()["items"][0]["version"] == 2


def test_concurrent_exchange_only_one_wins(shop: Shop, owner_engine: Engine) -> None:
    from concurrent.futures import ThreadPoolExecutor

    enable(shop)
    contact(shop)
    booking(shop)
    _, token = request_link(shop, owner_engine)

    def redeem(_: int) -> int:
        client = TestClient(shop.app, base_url="https://localhost:8443", headers=ORIGIN)
        return client.post(BASE + "/exchange", json={"token": token}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(redeem, [0, 1])) == [200, 403]


def test_cancel_inside_notice_without_configured_fee_and_scope_csrf(shop: Shop, owner_engine: Engine) -> None:
    client, appt = logged_in(shop, owner_engine)
    enable(shop, cancellation_notice_hours=200, cancellation_fee_text=None)
    assert (
        client.post(
            BASE + f"/bookings/{appt['id']}/cancel", json={"version": 1}, headers={"Origin": "null"}
        ).status_code
        == 403
    )
    assert client.post(BASE + f"/bookings/{appt['id']}/cancel", json={"version": 1}).status_code == 200


def test_session_idle_slides_after_successful_use(shop: Shop, owner_engine: Engine) -> None:
    client, _ = logged_in(shop, owner_engine)
    clock.set_clock(lambda: NOW + timedelta(minutes=50))
    r = client.get(BASE + "/bookings")
    assert r.status_code == 200 and "Max-Age=3600" in r.headers["set-cookie"]
    clock.set_clock(lambda: NOW + timedelta(minutes=100))
    assert client.get(BASE + "/bookings").status_code == 200
    clock.set_clock(lambda: NOW + timedelta(minutes=161))
    assert client.get(BASE + "/bookings").status_code == 403


def test_slot_search_rejects_naive_timestamps(shop: Shop, owner_engine: Engine) -> None:
    client, appt = logged_in(shop, owner_engine)
    r = client.get(
        BASE + f"/bookings/{appt['id']}/slots",
        params={"earliest": "2026-11-11T10:00:00", "latest": "2026-11-12T10:00:00"},
    )
    assert r.status_code == 422


def test_only_future_starts_are_listed_and_in_progress_cannot_change(shop: Shop, owner_engine: Engine) -> None:
    client, appt = logged_in(shop, owner_engine)
    with owner_engine.begin() as db:
        db.execute(
            text("update appointments set starts_at=:start, ends_at=:end, blocked_until=:blocked where id=:id"),
            {
                "start": NOW - timedelta(minutes=15),
                "end": NOW + timedelta(minutes=45),
                "blocked": NOW + timedelta(minutes=60),
                "id": appt["id"],
            },
        )
    assert client.get(BASE + "/bookings").json()["items"] == []
    assert client.post(BASE + f"/bookings/{appt['id']}/cancel", json={"version": 1}).status_code == 409


def test_new_customer_exchange_revokes_previous_browser_session(shop: Shop, owner_engine: Engine) -> None:
    client, ben_booking = logged_in(shop, owner_engine)
    previous = next(c.value for c in client.cookies.jar if c.name == "smb_self_service")
    assert (
        shop.owner.post(
            f"/api/v1/crm/customers/{shop.ada}/contact-points",
            json={"type": "email", "value": "ada@example.com", "is_primary": True},
        ).status_code
        == 201
    )
    ada = shop.book(shop.tuneup, shop.ada, "2026-11-10T20:00:00Z").json()
    assert client.post(BASE + "/request", json={"email": "ada@example.com"}).status_code == 202
    with owner_engine.connect() as db:
        link = db.execute(
            text(
                "select variables->>'link' from outbox_messages "
                "where recipient='ada@example.com' and template_key='self_service_link'"
            )
        ).scalar_one()
    assert client.post(BASE + "/exchange", json={"token": link.split("#t=")[1]}).status_code == 200
    rows = client.get(BASE + "/bookings").json()["items"]
    assert [r["id"] for r in rows] == [ada["id"]] and ada["id"] != ben_booking["id"]
    # A delayed HTTP response carrying the old cookie cannot restore the previous customer.
    assert client.get(BASE + "/bookings", headers={"Cookie": f"smb_self_service={previous}"}).status_code == 403
