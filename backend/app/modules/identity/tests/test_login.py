"""P2-03: Keycloak login (backend-for-frontend) and sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from joserfc.jwk import RSAKey

from app.modules.identity.login import safe_return_to
from app.modules.identity.testing import add_member, login_as, make_user
from app.platform import clock
from app.platform.permissions import SESSION_COOKIE

from .conftest import FakeKeycloak, https_client, make_business, start_login


def test_full_login_creates_session_cookie_and_me(app: Any, keycloak: FakeKeycloak) -> None:
    business = make_business("example-cycle-works")
    user = make_user("morgan@example.com", "Morgan Example")
    # existing user row must carry the Keycloak subject
    from sqlalchemy import text

    from app.platform.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(text("update users set oidc_subject = 'kc-sub-1' where id = :u"), {"u": user})
    add_member(user, business, ["owner"])
    client = https_client(app)
    login = start_login(client)
    keycloak.next_claims = {"nonce": login["nonce"]}
    response = client.get("/api/v1/auth/callback", params={"state": login["state"], "code": "abc"})
    assert response.status_code == 302 and response.headers["location"] == "/customers"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie.replace("samesite", "SameSite")
    me = client.get("/api/v1/auth/me").json()
    assert me["active_business_id"] == str(business) and "org.business.admin" in me["permissions"]
    assert me["csrf_token"] and me["active_business_timezone"] == "America/Denver"
    assert client.get("/api/v1/auth/me").json()["csrf_token"] == me["csrf_token"]  # stable per session


def test_login_start_is_rate_limited_and_says_when_to_retry(app: Any, keycloak: FakeKeycloak, freeze_time: Any) -> None:
    """30 starts per minute per address; the 31st gets the standard Retry-After (F7) for what is left of the minute."""
    freeze_time(datetime(2026, 11, 5, 17, 0, 20, tzinfo=UTC))
    client = https_client(app)
    assert [client.get("/api/v1/auth/login").status_code for _ in range(30)] == [302] * 30
    refused = client.get("/api/v1/auth/login")
    assert refused.status_code == 429 and refused.json()["code"] == "platform.rate_limited"
    assert refused.headers["Retry-After"] == "40" and refused.json()["retry_after_seconds"] == 40


@pytest.mark.parametrize(
    "tamper",
    ["bad_state", "wrong_nonce", "expired", "wrong_issuer", "wrong_audience", "foreign_key", "provider_error"],
)
def test_callback_rejects_bad_state_nonce_and_expired_token(app: Any, keycloak: FakeKeycloak, tamper: str) -> None:
    client = https_client(app)
    login = start_login(client)
    claims: dict[str, Any] = {"nonce": login["nonce"]}
    params = {"state": login["state"], "code": "abc"}
    if tamper == "bad_state":
        params["state"] = "forged"
    elif tamper == "wrong_nonce":
        claims["nonce"] = "other"
    elif tamper == "expired":
        claims["exp_offset"] = -3600
    elif tamper == "wrong_issuer":
        claims["iss"] = "https://evil.test/realms/smb"
    elif tamper == "wrong_audience":
        claims["aud"] = "another-client"
    elif tamper == "foreign_key":
        claims["key"] = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    elif tamper == "provider_error":
        params = {"error": "access_denied", "state": login["state"]}
    keycloak.next_claims = claims
    response = client.get("/api/v1/auth/callback", params=params)
    assert response.status_code == 302 and response.headers["location"] == "/login-failed"
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_state_is_single_use(app: Any, keycloak: FakeKeycloak) -> None:
    client = https_client(app)
    login = start_login(client)
    keycloak.next_claims = {"nonce": login["nonce"], "email": "new.person@example.com", "sub": "kc-new"}
    first = client.get("/api/v1/auth/callback", params={"state": login["state"], "code": "abc"})
    assert first.headers["location"] == "/select-business"  # new user, no business yet
    replay = https_client(app).get("/api/v1/auth/callback", params={"state": login["state"], "code": "abc"})
    assert replay.headers["location"] == "/login-failed"


def test_first_login_accepts_matching_invitation(app: Any, keycloak: FakeKeycloak) -> None:
    business = make_business("example-cycle-works")
    owner = login_as(app, business, ["owner"])
    owner.base_url = "https://testserver"
    invited = owner.post(
        "/api/v1/identity/invitations",
        json={"email": "jordan@example.com", "display_name": "Jordan Example", "roles": ["operator"]},
    )
    assert invited.status_code == 201 and invited.json()["temporary_password"]
    assert keycloak.created_users[0]["requiredActions"] == ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]
    client = https_client(app)
    login = start_login(client, "/inbox")
    keycloak.next_claims = {
        "nonce": login["nonce"],
        "sub": "kc-1",
        "email": "jordan@example.com",
        "name": "Jordan Example",
    }
    response = client.get("/api/v1/auth/callback", params={"state": login["state"], "code": "abc"})
    assert response.headers["location"] == "/inbox"
    me = client.get("/api/v1/auth/me").json()
    assert me["memberships"][0]["roles"] == ["operator"] and "scheduling.appointment.write" in me["permissions"]


def test_email_already_linked_to_other_subject_is_refused(app: Any, keycloak: FakeKeycloak) -> None:
    make_user("morgan@example.com")
    client = https_client(app)
    login = start_login(client)
    keycloak.next_claims = {"nonce": login["nonce"], "sub": "attacker-sub", "email": "morgan@example.com"}
    assert client.get("/api/v1/auth/callback", params=login | {"code": "x"}).headers["location"] == "/login-failed"


@pytest.mark.parametrize(
    "value", ["https://evil.example/x", "//evil.example", "javascript:alert(1)", "\\\\evil", "customers"]
)
def test_return_to_rejects_absolute_urls(value: str) -> None:
    assert safe_return_to(value) == "/today"
    assert safe_return_to("/customers?tab=notes") == "/customers?tab=notes"


def test_session_idle_and_absolute_timeout(app: Any) -> None:
    business = make_business("example-cycle-works")
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    clock.set_clock(lambda: start)
    client = login_as(app, business, ["operator"])
    assert client.get("/api/v1/org/business").status_code == 200
    clock.set_clock(lambda: start + timedelta(minutes=59))
    assert client.get("/api/v1/org/business").status_code == 200  # activity refreshes idle timer
    clock.set_clock(lambda: start + timedelta(minutes=59 + 61))
    assert client.get("/api/v1/org/business").status_code == 401  # idle > 60 min
    clock.set_clock(lambda: start)
    client2 = login_as(app, business, ["operator"])
    statuses = {}
    for minutes in range(50, 12 * 60 + 50, 50):  # stay active every 50 min, crossing the 12 h absolute limit
        clock.set_clock(lambda m=minutes: start + timedelta(minutes=m))
        statuses[minutes] = client2.get("/api/v1/org/business").status_code
    assert statuses[700] == 200 and statuses[750] == 401


def test_permissions_recomputed_each_request_after_role_revocation(app: Any) -> None:
    business = make_business("example-cycle-works")
    owner = login_as(app, business, ["owner"])
    staff_user = make_user("casey@example.com")
    membership = add_member(staff_user, business, ["admin"])
    staff = login_as(app, business, ["admin"], user_id=staff_user)
    assert staff.get("/api/v1/identity/members").status_code == 200
    members = {m["membership_id"]: m for m in owner.get("/api/v1/identity/members").json()}
    assert owner.put(f"/api/v1/identity/members/{membership}/roles", json={"roles": ["operator"]}).status_code == 200
    assert members  # sanity
    assert staff.get("/api/v1/identity/members").status_code == 403  # same session, new permissions


def test_session_rotated_on_business_switch_and_inactive_membership_refused(app: Any) -> None:
    cycle, garden, other = (
        make_business("example-cycle-works"),
        make_business("example-garden-store", "Europe/London"),
        make_business("other-shop"),
    )
    user = make_user("morgan@example.com")
    add_member(user, cycle, ["owner"])
    add_member(user, garden, ["operator"])
    add_member(user, other, ["owner"], status="suspended")
    client = login_as(app, cycle, ["owner"], user_id=user)
    client.base_url = "https://testserver"
    old_cookie = client.cookies.get(SESSION_COOKIE)
    switched = client.post("/api/v1/auth/active-business", json={"business_id": str(garden)})
    assert switched.status_code == 200 and switched.json()["active_business_timezone"] == "Europe/London"
    assert client.cookies.get(SESSION_COOKIE) != old_cookie
    stale = https_client(app)
    stale.cookies.set(SESSION_COOKIE, old_cookie or "")
    assert stale.get("/api/v1/auth/me").status_code == 401  # the old session was revoked
    client.headers["X-CSRF-Token"] = switched.json()["csrf_token"]
    assert {m["business_slug"] for m in switched.json()["memberships"]} == {
        "example-cycle-works",
        "example-garden-store",
    }
    refused = client.post("/api/v1/auth/active-business", json={"business_id": str(other)})
    assert refused.status_code == 403 and refused.json()["code"] == "identity.membership_inactive"


def test_logout_revokes_session(app: Any, keycloak: FakeKeycloak) -> None:
    business = make_business("example-cycle-works")
    client = login_as(app, business, ["owner"])
    client.base_url = "https://testserver"
    out = client.post("/api/v1/auth/logout")
    assert out.status_code == 200 and out.json()["logout_url"].startswith(
        "https://smb.test/auth/realms/smb/protocol/openid-connect/logout"
    )
    assert client.get("/api/v1/auth/me").status_code == 401


def test_pending_identity_is_claimed_by_first_login(app: Any, keycloak: FakeKeycloak) -> None:
    from app.modules.identity import public as identity
    from app.platform.context import Context
    from app.platform.db import transaction

    business = make_business("example-cycle-works")
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business)
    with transaction(ctx) as db:
        user_id = identity.ensure_user(db, "casey@example.com", "Casey Example")
        identity.ensure_membership(db, ctx, user_id, ["operator"], "Front desk")
    client = https_client(app)
    login = start_login(client, "/inbox")
    keycloak.next_claims = {
        "nonce": login["nonce"],
        "sub": "kc-casey",
        "email": "casey@example.com",
        "name": "Casey Example",
    }
    assert (
        client.get("/api/v1/auth/callback", params={"state": login["state"], "code": "x"}).headers["location"]
        == "/inbox"
    )
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["id"] == str(user_id) and me["memberships"][0]["roles"] == ["operator"]
