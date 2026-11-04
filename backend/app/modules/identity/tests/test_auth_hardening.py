"""Browser correlation, replacement sessions, owner authority and absolute expiry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.identity.testing import add_member, login_as, make_user
from app.platform import clock
from app.platform.permissions import SESSION_COOKIE

from .conftest import FakeKeycloak, https_client, make_business, start_login


@pytest.mark.parametrize("other_login", [False, True])
def test_callback_requires_initiating_browser_without_consuming_state(
    app: Any, keycloak: FakeKeycloak, other_login: bool
) -> None:
    initiating, other = https_client(app), https_client(app)
    attempt = start_login(initiating)
    if other_login:
        start_login(other)
    keycloak.next_claims = {"nonce": attempt["nonce"]}
    callback = {"state": attempt["state"], "code": "unused-code"}
    refused = other.get("/api/v1/auth/callback", params=callback)
    assert refused.headers["location"] == "/login-failed"
    assert SESSION_COOKIE not in other.cookies
    accepted = initiating.get("/api/v1/auth/callback", params=callback)
    assert accepted.headers["location"] == "/select-business"
    assert SESSION_COOKIE in initiating.cookies
    assert initiating.get("/api/v1/auth/callback", params=callback).headers["location"] == "/login-failed"


@pytest.mark.parametrize("same_user", [False, True])
def test_successful_login_revokes_presented_session_only(app: Any, keycloak: FakeKeycloak, same_user: bool) -> None:
    client = https_client(app)
    first = start_login(client)
    keycloak.next_claims = {"nonce": first["nonce"]}
    client.get("/api/v1/auth/callback", params={"state": first["state"], "code": "first"})
    old_token = client.cookies.get(SESSION_COOKIE)
    old_user = client.get("/api/v1/auth/me").json()["user"]["id"]
    # Another session for the old user must survive replacement of this browser's session.
    from uuid import UUID

    independent = login_as(app, None, [], user_id=UUID(old_user))
    second = start_login(client)
    keycloak.next_claims = {"nonce": second["nonce"]}
    if not same_user:
        keycloak.next_claims.update(sub="other-subject", email="other@example.com")
    client.get("/api/v1/auth/callback", params={"state": second["state"], "code": "second"})
    assert client.cookies.get(SESSION_COOKIE) != old_token
    stolen = https_client(app)
    stolen.cookies.set(SESSION_COOKIE, old_token or "")
    assert stolen.get("/api/v1/auth/me").status_code == 401
    assert independent.get("/api/v1/auth/me").status_code == 200
    client.headers["X-CSRF-Token"] = client.get("/api/v1/auth/me").json()["csrf_token"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert stolen.get("/api/v1/auth/me").status_code == 401


def test_failed_login_keeps_presented_session(app: Any, keycloak: FakeKeycloak) -> None:
    client = login_as(app, None, [])
    client.follow_redirects = False
    original = client.cookies.get(SESSION_COOKIE)
    attempt = start_login(client)
    keycloak.next_claims = {"nonce": "wrong-nonce"}
    assert (
        client.get("/api/v1/auth/callback", params={"state": attempt["state"], "code": "invalid"}).headers["location"]
        == "/login-failed"
    )
    assert client.cookies.get(SESSION_COOKIE) == original
    assert client.get("/api/v1/auth/me").status_code == 200


def test_admin_cannot_reactivate_owner_but_owner_can(app: Any) -> None:
    business = make_business("owner-reactivation")
    target = add_member(make_user(), business, ["owner"], status="suspended")
    admin = login_as(app, business, ["admin"])
    members = admin.get("/api/v1/identity/members").json()
    version = next(m["version"] for m in members if m["membership_id"] == str(target))
    path = f"/api/v1/identity/members/{target}"
    response = admin.patch(path, json={"version": version, "status": "active"})
    assert response.status_code == 403 and response.json()["code"] == "identity.owner_grant_forbidden"
    owner = login_as(app, business, ["owner"])
    assert owner.patch(path, json={"version": version, "status": "active"}).status_code == 200


@pytest.mark.parametrize("same_business", [False, True])
def test_standard_rotation_preserves_original_absolute_deadline(
    app: Any, owner_engine: Engine, same_business: bool
) -> None:
    start = datetime(2026, 11, 5, 17, tzinfo=UTC)
    clock.set_clock(lambda: start)
    business, other = make_business("origin"), make_business("destination")
    client = login_as(app, business, ["owner"])
    add_member(client.user_id, other, ["owner"])
    for hours in (10, 11):
        now = start + timedelta(hours=hours)
        clock.set_clock(lambda now=now: now)
        with owner_engine.begin() as conn:
            conn.execute(text("update sessions set last_seen_at=:now where revoked_at is null"), {"now": now})
        response = client.post(
            "/api/v1/auth/active-business", json={"business_id": str(business if same_business else other)}
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        with owner_engine.connect() as conn:
            assert conn.execute(text("select expires_at from sessions where revoked_at is null")).scalar_one() == (
                start + timedelta(hours=12)
            )
    now = start + timedelta(hours=13)
    clock.set_clock(lambda: now)
    with owner_engine.begin() as conn:
        conn.execute(text("update sessions set last_seen_at=:now where revoked_at is null"), {"now": now})
    assert client.get("/api/v1/auth/me").status_code == 401
