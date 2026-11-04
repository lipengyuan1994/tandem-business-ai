"""Keycloak logins for demo staff: what is sent to Keycloak, and when an account is left alone (`demo_logins.py`)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from app.modules.identity import keycloak_admin
from app.modules.identity import public as identity
from app.modules.identity.demo_logins import StaffIdentity, active_staff, ensure_demo_login
from app.modules.identity.keycloak_admin import KeycloakError
from app.modules.identity.testing import add_member, make_user
from app.platform.context import Context
from app.platform.db import transaction

from .conftest import FakeKeycloak, make_business

RILEY = StaffIdentity("riley@example.com", "Riley Example", linked_subject=None, active=True)


# ------------------------------------------------------------------------------------------------- creating
def test_a_demo_login_is_created_with_a_permanent_password_and_the_second_factor_still_required(
    keycloak: FakeKeycloak,
) -> None:
    result = ensure_demo_login(RILEY, reset_passwords=False, temporary_password=False)
    assert result.state == "created" and result.password and len(result.password) >= 12
    (body,) = keycloak.created_users
    assert (body["username"], body["email"], body["firstName"], body["lastName"]) == (
        "riley@example.com",
        "riley@example.com",
        "Riley",
        "Example",
    )
    assert body["enabled"] and body["emailVerified"]
    assert body["credentials"] == [{"type": "password", "value": result.password, "temporary": False}]
    assert body["requiredActions"] == ["CONFIGURE_TOTP"]  # no forced password change; the authenticator app stays


def test_the_temporary_password_flag_makes_keycloak_force_a_change_at_first_sign_in(keycloak: FakeKeycloak) -> None:
    result = ensure_demo_login(RILEY, reset_passwords=False, temporary_password=True)
    (body,) = keycloak.created_users
    assert body["credentials"] == [{"type": "password", "value": result.password, "temporary": True}]
    assert body["requiredActions"] == ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]


def test_invitations_and_bootstrap_still_create_temporary_accounts(keycloak: FakeKeycloak) -> None:
    """`create_user` gained keyword options for the demo; callers that pass none get what they always got."""
    keycloak_admin.create_user("owner@example.com", "Alex Owner", "Temp-Pass-12345")
    (body,) = keycloak.created_users
    assert body["credentials"][0]["temporary"] is True
    assert body["requiredActions"] == ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]


def test_each_login_gets_its_own_random_password(keycloak: FakeKeycloak) -> None:
    other = StaffIdentity("morgan@example.com", "Morgan Example", None, True)
    first, second = (
        ensure_demo_login(RILEY, reset_passwords=False, temporary_password=False),
        ensure_demo_login(other, reset_passwords=False, temporary_password=False),
    )
    assert first.password != second.password


def test_the_result_does_not_show_the_password_when_printed(keycloak: FakeKeycloak) -> None:
    result = ensure_demo_login(RILEY, reset_passwords=False, temporary_password=False)
    assert result.password and result.password not in repr(result) + str(result)


# ------------------------------------------------------------------------------------- re-runs and resets
def test_an_existing_account_is_left_alone(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user("riley@example.com", "kc-riley")
    result = ensure_demo_login(RILEY, reset_passwords=False, temporary_password=False)
    assert (result.state, result.password) == ("exists", None)
    assert keycloak.created_users == [] and keycloak.password_resets == []


def test_reset_passwords_replaces_the_password_and_cancels_a_pending_password_change(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user("riley@example.com", "kc-riley", required_actions=["UPDATE_PASSWORD"])
    result = ensure_demo_login(RILEY, reset_passwords=True, temporary_password=False)
    assert result.state == "reset" and result.password
    assert keycloak.password_resets == [
        ("kc-riley", {"type": "password", "value": result.password, "temporary": False})
    ]
    user = keycloak.users["kc-riley"]
    assert user["requiredActions"] == []  # the flag said "no forced change"; Keycloak would have kept the action
    assert (user["email"], user["firstName"], user["enabled"]) == ("riley@example.com", "Existing", True)
    assert keycloak.created_users == []


def test_reset_with_the_temporary_flag_forces_a_change(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user("riley@example.com", "kc-riley")
    result = ensure_demo_login(RILEY, reset_passwords=True, temporary_password=True)
    assert keycloak.password_resets[0][1] == {"type": "password", "value": result.password, "temporary": True}
    assert "UPDATE_PASSWORD" in keycloak.users["kc-riley"]["requiredActions"]


def test_a_reset_leaves_other_pending_actions_alone(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user("riley@example.com", "kc-riley", required_actions=["UPDATE_PASSWORD", "CONFIGURE_TOTP"])
    ensure_demo_login(RILEY, reset_passwords=True, temporary_password=False)
    assert keycloak.users["kc-riley"]["requiredActions"] == ["CONFIGURE_TOTP"]


# ------------------------------------------------------------------------- people who already signed in
def test_a_person_who_signed_in_before_needs_the_account_they_signed_in_with(keycloak: FakeKeycloak) -> None:
    morgan = StaffIdentity("morgan@example.com", "Morgan Example", linked_subject="kc-morgan", active=True)
    # their Keycloak account is gone: a new one would get another id and the sign-in would be refused
    gone = ensure_demo_login(morgan, reset_passwords=True, temporary_password=False)
    assert gone.state == "conflict" and gone.password is None and keycloak.created_users == []
    # an account with that address exists but is not the one the application knows
    keycloak.add_existing_user("morgan@example.com", "kc-someone-else")
    other = ensure_demo_login(morgan, reset_passwords=True, temporary_password=False)
    assert other.state == "conflict" and other.password is None and keycloak.password_resets == []
    # their own account: normal rules
    keycloak.users.clear()
    keycloak.add_existing_user("morgan@example.com", "kc-morgan")
    assert ensure_demo_login(morgan, reset_passwords=False, temporary_password=False).state == "exists"
    assert ensure_demo_login(morgan, reset_passwords=True, temporary_password=False).state == "reset"


def test_an_account_that_appears_meanwhile_is_not_reported_as_created(
    keycloak: FakeKeycloak, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two operators at once: the second create is refused (409) and its password is never recorded as the login's."""
    keycloak.add_existing_user("riley@example.com", "kc-riley")
    monkeypatch.setattr(keycloak_admin, "find_user_id", lambda email: None)  # the lookup ran before the other create
    with pytest.raises(KeycloakError, match="HTTP 409"):
        ensure_demo_login(RILEY, reset_passwords=False, temporary_password=False)
    assert keycloak.created_users == []


def test_a_keycloak_failure_is_an_error_not_a_login(keycloak: FakeKeycloak) -> None:
    keycloak.fail_create_for.add("riley@example.com")
    with pytest.raises(KeycloakError, match="HTTP 500"):
        ensure_demo_login(RILEY, reset_passwords=False, temporary_password=False)


# ------------------------------------------------------------------------------------------- who is on staff
def test_active_staff_lists_the_active_members_of_one_business_and_whether_they_signed_in(
    owner_engine: Engine,
) -> None:
    cycle, garden = make_business("example-cycle-works"), make_business("example-garden-store")
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=cycle)
    with transaction(ctx) as db:
        pending = identity.ensure_user(db, "riley@example.com", "Riley Example")  # the seed's kind of user
        identity.ensure_membership(db, ctx, pending, ["admin"], "Office manager")
    signed_in = make_user("morgan@example.com", "Morgan Example")  # bound to a Keycloak subject
    add_member(signed_in, cycle, ["owner"])
    add_member(make_user("casey@example.com"), cycle, ["operator"], status="suspended")
    add_member(make_user("sam@example.com"), garden, ["owner"])
    disabled = make_user("quinn@example.com")
    add_member(disabled, cycle, ["auditor"])
    with owner_engine.begin() as conn:
        conn.execute(text("update users set status = 'disabled' where id = :u"), {"u": disabled})

    with transaction(ctx) as db:
        staff = active_staff(db, ctx)
    assert sorted(staff) == ["morgan@example.com", "quinn@example.com", "riley@example.com"]
    assert staff["riley@example.com"].linked_subject is None and staff["riley@example.com"].active
    assert staff["morgan@example.com"].linked_subject == f"sub-{signed_in}"
    assert staff["quinn@example.com"].active is False
