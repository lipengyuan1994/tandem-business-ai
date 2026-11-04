"""OIDC authorization-code + PKCE client for Keycloak (backend-for-frontend; ADR-004).

Back-channel calls (discovery, token, JWKS) go to OIDC_INTERNAL_ISSUER_URL only — an internal service address,
which is why this module uses its own pinned client instead of platform.http (that one is for internet egress).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from app.platform.config import get_settings
from app.platform.errors import DomainError

ALLOWED_ALGS = ["RS256"]
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
CACHE_SECONDS = 600


class LoginFailed(DomainError):
    status = 400
    title = "Login failed"

    def __init__(self, reason: str) -> None:
        # The reason is logged server-side by the caller; the client sees only the code.
        super().__init__("identity.login_failed")
        self.reason = reason


class KeycloakUnavailable(DomainError):
    status = 503
    title = "Identity provider unavailable"


@dataclass(frozen=True)
class LoginRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False)


def _cached(key: str, loader: Any) -> Any:
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    value = loader()
    with _lock:
        _cache[key] = (time.monotonic() + CACHE_SECONDS, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def discovery() -> dict[str, Any]:
    url = get_settings().oidc_internal_issuer_url.rstrip("/") + "/.well-known/openid-configuration"

    def load() -> dict[str, Any]:
        try:
            with _client() as c:
                response = c.get(url)
                response.raise_for_status()
                return dict(response.json())
        except httpx.HTTPError as exc:
            raise KeycloakUnavailable("identity.keycloak_unavailable") from exc

    return dict(_cached("discovery", load))


def _jwks() -> Any:
    def load() -> Any:
        try:
            with _client() as c:
                response = c.get(discovery()["jwks_uri"])
                response.raise_for_status()
                return KeySet.import_key_set(response.json())
        except httpx.HTTPError as exc:
            raise KeycloakUnavailable("identity.keycloak_unavailable") from exc

    return _cached("jwks", load)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def redirect_uri() -> str:
    return get_settings().app_base_url.rstrip("/") + "/api/v1/auth/callback"


def build_login_request() -> LoginRequest:
    settings = get_settings()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": redirect_uri(),
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return LoginRequest(
        url=f"{discovery()['authorization_endpoint']}?{query}", state=state, nonce=nonce, code_verifier=verifier
    )


def exchange_code(code: str, code_verifier: str) -> str:
    """Code → ID token (raw JWT)."""
    settings = get_settings()
    try:
        with _client() as c:
            response = c.post(
                discovery()["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri(),
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret.get_secret_value(),
                    "code_verifier": code_verifier,
                },
            )
    except httpx.HTTPError as exc:
        raise KeycloakUnavailable("identity.keycloak_unavailable") from exc
    if response.status_code != 200:
        raise LoginFailed(f"token endpoint returned {response.status_code}")
    id_token = response.json().get("id_token")
    if not id_token:
        raise LoginFailed("no id_token in token response")
    return str(id_token)


def validate_id_token(id_token: str, nonce: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        token = jwt.decode(id_token, _jwks(), algorithms=ALLOWED_ALGS)
        jwt.JWTClaimsRegistry(
            leeway=30,
            iss={"essential": True, "value": settings.oidc_issuer},
            aud={"essential": True, "value": settings.oidc_client_id},
            exp={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(token.claims)
    except (JoseError, ValueError) as exc:
        raise LoginFailed(f"invalid id token: {exc}") from exc
    claims = dict(token.claims)
    if not claims.get("email"):
        raise LoginFailed("id token has no email")
    return claims


def end_session_url() -> str:
    settings = get_settings()
    endpoint = discovery().get("end_session_endpoint")
    if not endpoint:
        return settings.app_base_url
    query = urlencode({"client_id": settings.oidc_client_id, "post_logout_redirect_uri": settings.app_base_url})
    return f"{endpoint}?{query}"
