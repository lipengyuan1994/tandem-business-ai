"""Public judge account: actual Keycloak HTTP calls, including inherited account authority."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.demo_logins import JUDGE_PASSWORD_MIN_LENGTH
from app.modules.identity import keycloak_admin
from app.modules.identity.demo_logins import StaffIdentity, ensure_judge_login

from .conftest import FakeKeycloak

BASE = "http://keycloak.test/auth/admin/realms/smb/users/kc-judge"
JUDGE = StaffIdentity("judge@example.com", "Demo Judge", None, True)


def test_hardening_clears_required_actions_otp_defaults_groups_and_direct_account_roles(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user(JUDGE.email, "kc-judge", required_actions=["CONFIGURE_TOTP", "UPDATE_PASSWORD"])
    keycloak.users["kc-judge"].update(enabled=False, emailVerified=False)
    defaults = [{"id": "default", "name": "default-roles-smb", "composite": True}]
    account_roles = [{"id": name, "name": name} for name in ("manage-account", "view-profile", "manage-account-links")]
    router = keycloak.router
    router.get(BASE + "/groups").mock(return_value=httpx.Response(200, json=[{"id": "inherited-account-access"}]))
    group_removal = router.delete(BASE + "/groups/inherited-account-access").mock(return_value=httpx.Response(204))
    router.get(BASE + "/role-mappings").mock(
        return_value=httpx.Response(
            200,
            json={
                "realmMappings": defaults,
                "clientMappings": {"account": {"id": "account-id", "mappings": account_roles}},
            },
        )
    )
    realm_removal = router.delete(BASE + "/role-mappings/realm").mock(return_value=httpx.Response(204))
    account_removal = router.delete(BASE + "/role-mappings/clients/account-id").mock(return_value=httpx.Response(204))
    router.get(BASE + "/credentials").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "password-id", "type": "password"},
                {"id": "otp-id", "type": "otp"},
                {"id": "passkey-id", "type": "webauthn-passwordless"},
            ],
        )
    )
    otp_removal = router.delete(BASE + "/credentials/otp-id").mock(return_value=httpx.Response(204))
    passkey_removal = router.delete(BASE + "/credentials/passkey-id").mock(return_value=httpx.Response(204))
    logout = router.post(BASE + "/logout").mock(return_value=httpx.Response(204))
    result = ensure_judge_login(JUDGE, "Public-judge-test-2026", reset_password=False)
    assert result.state == "exists" and result.password is None
    assert group_removal.called and otp_removal.called and passkey_removal.called and logout.called
    assert json.loads(realm_removal.calls.last.request.content) == defaults
    assert json.loads(account_removal.calls.last.request.content) == account_roles
    user = keycloak.users["kc-judge"]
    assert user["requiredActions"] == [] and user["enabled"] and user["emailVerified"]
    assert user["firstName"] == "Demo" and user["lastName"] == "Judge"
    assert keycloak.password_resets == []


def test_hardening_failure_does_not_report_success_or_echo_keycloak_body(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user(JUDGE.email, "kc-judge")
    keycloak.router.get(BASE + "/groups").mock(return_value=httpx.Response(403, text="sensitive-response"))
    with pytest.raises(keycloak_admin.KeycloakError, match="HTTP 403") as caught:
        ensure_judge_login(JUDGE, "Public-judge-test-2026", reset_password=True)
    assert "sensitive-response" not in str(caught.value)
    assert keycloak.password_resets == []


def test_judge_never_rebinds_an_existing_staff_identity(keycloak: FakeKeycloak) -> None:
    person = StaffIdentity(JUDGE.email, JUDGE.display_name, "kc-original", True)
    assert ensure_judge_login(person, "Public-judge-test-2026", reset_password=True).state == "conflict"
    keycloak.add_existing_user(JUDGE.email, "kc-different")
    assert ensure_judge_login(person, "Public-judge-test-2026", reset_password=True).state == "conflict"
    assert not keycloak.password_resets and not keycloak.created_users


def test_judge_minimum_matches_both_bundled_realm_policies() -> None:
    root = Path(__file__).resolve().parents[5]
    for realm in ("deploy/compose/realm-smb.prod.json", "deploy/keycloak/realm-smb.dev.json"):
        policy = json.loads((root / realm).read_text())["passwordPolicy"]
        assert f"length({JUDGE_PASSWORD_MIN_LENGTH})" in policy


def test_explicit_reset_can_reapply_same_public_password_despite_password_history(keycloak: FakeKeycloak) -> None:
    keycloak.add_existing_user(JUDGE.email, "kc-judge")
    credentials = [{"id": "current", "type": "password"}, {"id": "old", "type": "password-history"}]
    keycloak.router.get(BASE + "/credentials").mock(return_value=httpx.Response(200, json=credentials))
    current = keycloak.router.delete(BASE + "/credentials/current").mock(return_value=httpx.Response(204))
    history = keycloak.router.delete(BASE + "/credentials/old").mock(return_value=httpx.Response(204))
    for _ in range(2):
        keycloak_admin.reset_judge_password("kc-judge", "Public-judge-test-2026")
    assert current.call_count == history.call_count == 2
    assert len(keycloak.password_resets) == 2
    assert all(
        r[1] == {"type": "password", "value": "Public-judge-test-2026", "temporary": False}
        for r in keycloak.password_resets
    )
