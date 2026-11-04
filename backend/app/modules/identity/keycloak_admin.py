"""Minimal Keycloak Admin REST client using the `smb-admin` service account (manage-users only).

Back-channel to OIDC_INTERNAL_ISSUER_URL's server only. Passwords created here are temporary by default: Keycloak
forces the user to change them (and to set up TOTP) at first login. The demo logins (`demo_logins.py`) are the one
caller that asks for permanent passwords; the authenticator app stays mandatory for them too.
"""

from __future__ import annotations

import secrets
import string
from typing import Any

import httpx

from app.platform.config import get_settings

from .oidc import KeycloakUnavailable, discovery

REQUIRED_ACTIONS = ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]
# Permanent password, second factor still mandatory (the realm also lists CONFIGURE_TOTP as a default action).
TOTP_ONLY = ["CONFIGURE_TOTP"]


class KeycloakError(KeycloakUnavailable):
    pass


def _admin_base() -> str:
    issuer = get_settings().oidc_internal_issuer_url.rstrip("/")  # http://keycloak:8080/auth/realms/smb
    root, realm = issuer.rsplit("/realms/", 1)
    return f"{root}/admin/realms/{realm}"


def _token(client: httpx.Client) -> str:
    settings = get_settings()
    response = client.post(
        discovery()["token_endpoint"],
        data={
            "grant_type": "client_credentials",
            "client_id": settings.keycloak_admin_client_id,
            "client_secret": settings.keycloak_admin_client_secret.get_secret_value(),
        },
    )
    if response.status_code != 200:
        raise KeycloakError("identity.keycloak_unavailable", detail=f"admin token: HTTP {response.status_code}")
    return str(response.json()["access_token"])


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False) as client:
            headers = {"Authorization": f"Bearer {_token(client)}"}
            return client.request(method, _admin_base() + path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise KeycloakError("identity.keycloak_unavailable") from exc


def temporary_password() -> str:
    """16 characters with upper, lower, digit and symbol (satisfies the realm's length-12 policy)."""
    alphabet = string.ascii_letters + string.digits + "-_.!"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def find_user_id(email: str) -> str | None:
    response = _request("GET", "/users", params={"email": email, "exact": "true"})
    if response.status_code != 200:
        raise KeycloakError("identity.keycloak_unavailable", detail=f"find user: HTTP {response.status_code}")
    users = response.json()
    return str(users[0]["id"]) if users else None


def create_user(
    email: str,
    display_name: str,
    password: str | None,
    *,
    temporary: bool = True,
    required_actions: list[str] | None = None,
    existing_ok: bool = True,
) -> str:
    """Create the account and return its id (the OIDC `sub`).

    `temporary=False` makes the password permanent (no forced change; pass `required_actions=TOTP_ONLY` with it).
    `existing_ok=False` turns "already exists" (409) into an error instead of returning the other account's id, for
    callers that must know the password they chose is the one that is set.
    """
    first, _, last = display_name.partition(" ")
    body: dict[str, Any] = {
        "username": email,
        "email": email,
        "firstName": first,
        "lastName": last,
        "enabled": True,
        "emailVerified": True,
        "requiredActions": list(REQUIRED_ACTIONS if required_actions is None else required_actions),
    }
    if password:
        body["credentials"] = [{"type": "password", "value": password, "temporary": temporary}]
    response = _request("POST", "/users", json=body)
    if response.status_code == 409 and existing_ok:
        existing = find_user_id(email)
        if existing:
            return existing
    if response.status_code != 201:
        raise KeycloakError("identity.keycloak_unavailable", detail=f"create user: HTTP {response.status_code}")
    return response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


def set_password(user_id: str, password: str, *, temporary: bool) -> None:
    """Replace a user's password; the authenticator app (TOTP) they already set up is untouched.

    Keycloak adds UPDATE_PASSWORD for a temporary password but never removes it for a permanent one, so a permanent
    password also clears a pending UPDATE_PASSWORD: the flag decides whether the person is asked to change it.
    """
    credential = {"type": "password", "value": password, "temporary": temporary}
    response = _request("PUT", f"/users/{user_id}/reset-password", json=credential)
    if response.status_code not in (200, 204):
        raise KeycloakError("identity.keycloak_unavailable", detail=f"reset password: HTTP {response.status_code}")
    if not temporary:
        _drop_required_action(user_id, "UPDATE_PASSWORD")


def _drop_required_action(user_id: str, action: str) -> None:
    response = _request("GET", f"/users/{user_id}")
    if response.status_code != 200:
        raise KeycloakError("identity.keycloak_unavailable", detail=f"read user: HTTP {response.status_code}")
    user = response.json()
    pending = [a for a in user.get("requiredActions") or [] if a != action]
    if len(pending) == len(user.get("requiredActions") or []):
        return
    user["requiredActions"] = pending  # the whole representation goes back, so nothing else about the user changes
    response = _request("PUT", f"/users/{user_id}", json=user)
    if response.status_code not in (200, 204):
        raise KeycloakError("identity.keycloak_unavailable", detail=f"update user: HTTP {response.status_code}")


def delete_user(user_id: str) -> None:
    response = _request("DELETE", f"/users/{user_id}")
    if response.status_code not in (204, 404):
        raise KeycloakError("identity.keycloak_unavailable", detail=f"delete user: HTTP {response.status_code}")


def send_setup_email(user_id: str) -> None:
    response = _request("PUT", f"/users/{user_id}/execute-actions-email", json=REQUIRED_ACTIONS)
    if response.status_code not in (200, 204):
        raise KeycloakError("identity.keycloak_unavailable", detail=f"actions email: HTTP {response.status_code}")


def set_enabled(user_id: str, enabled: bool) -> None:
    response = _request("PUT", f"/users/{user_id}", json={"enabled": enabled})
    if response.status_code not in (200, 204):
        raise KeycloakError("identity.keycloak_unavailable", detail=f"update user: HTTP {response.status_code}")
