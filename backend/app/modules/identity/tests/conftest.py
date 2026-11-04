from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from app.main import create_app
from app.modules.identity import oidc
from app.modules.org import public as org
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.registry import Registry

INTERNAL = "http://keycloak.test/auth/realms/smb"
ISSUER = "https://smb.test/auth/realms/smb"


@pytest.fixture(autouse=True)
def oidc_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_INTERNAL_ISSUER_URL", INTERNAL)
    monkeypatch.setenv("OIDC_CLIENT_ID", "smb-app")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "unit-test")
    monkeypatch.setenv("KEYCLOAK_ADMIN_CLIENT_SECRET", "unit-test")
    monkeypatch.setenv("APP_BASE_URL", "https://smb.test")
    monkeypatch.delenv("SMTP_URL", raising=False)
    get_settings.cache_clear()
    oidc.clear_cache()
    yield
    get_settings.cache_clear()
    oidc.clear_cache()


class FakeKeycloak:
    def __init__(self, router: respx.MockRouter) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"})
        self.router = router
        self.next_claims: dict[str, Any] = {}
        self.created_users: list[dict[str, Any]] = []
        self.users: dict[str, dict[str, Any]] = {}  # Keycloak id -> representation, for lookups and updates
        self.password_resets: list[tuple[str, dict[str, Any]]] = []  # (user id, credential sent)
        self.fail_create_for: set[str] = set()  # e-mail addresses whose creation answers 500
        router.get(f"{INTERNAL}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(
                200,
                json={
                    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                    "token_endpoint": f"{INTERNAL}/protocol/openid-connect/token",
                    "jwks_uri": f"{INTERNAL}/protocol/openid-connect/certs",
                    "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
                },
            )
        )
        router.get(f"{INTERNAL}/protocol/openid-connect/certs").mock(
            return_value=httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        )
        router.post(f"{INTERNAL}/protocol/openid-connect/token").mock(side_effect=self._token)
        admin = "http://keycloak.test/auth/admin/realms/smb"
        router.get(f"{admin}/users").mock(side_effect=self._find_users)
        router.post(f"{admin}/users").mock(side_effect=self._create_user)
        one_user = r"^/auth/admin/realms/smb/users/(?P<user_id>[^/]+)"
        router.get(host="keycloak.test", path__regex=one_user + "$").mock(side_effect=self._get_user)
        router.put(host="keycloak.test", path__regex=one_user + "$").mock(side_effect=self._put_user)
        router.put(host="keycloak.test", path__regex=one_user + "/reset-password$").mock(
            side_effect=self._reset_password
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["client_credentials"]:
            return httpx.Response(200, json={"access_token": "admin-token"})
        return httpx.Response(200, json={"id_token": self.id_token(**self.next_claims)})

    def _find_users(self, request: httpx.Request) -> httpx.Response:
        email = request.url.params.get("email", "").lower()
        return httpx.Response(200, json=[u for u in self.users.values() if u["email"].lower() == email])

    def _create_user(self, request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        if body["email"] in self.fail_create_for:
            return httpx.Response(500)
        if any(u["email"].lower() == body["email"].lower() for u in self.users.values()):
            return httpx.Response(409, json={"errorMessage": "User exists with same username"})
        self.created_users.append(body)
        user_id = f"kc-{len(self.created_users)}"
        self.users[user_id] = {
            "id": user_id,
            "username": body["email"],
            "email": body["email"],
            "firstName": body.get("firstName", ""),
            "lastName": body.get("lastName", ""),
            "enabled": True,
            "emailVerified": True,
            "requiredActions": list(body.get("requiredActions", [])),
        }
        return httpx.Response(201, headers={"Location": f"http://keycloak.test/auth/admin/realms/smb/users/{user_id}"})

    def add_existing_user(self, email: str, user_id: str, required_actions: list[str] | None = None) -> str:
        """An account that was there before the code under test ran (invited, bootstrapped, created by hand)."""
        self.users[user_id] = {
            "id": user_id,
            "username": email,
            "email": email,
            "firstName": "Existing",
            "lastName": "Person",
            "enabled": True,
            "emailVerified": True,
            "requiredActions": list(required_actions or []),
        }
        return user_id

    def _get_user(self, request: httpx.Request, user_id: str) -> httpx.Response:
        user = self.users.get(user_id)
        return httpx.Response(200, json=user) if user else httpx.Response(404)

    def _put_user(self, request: httpx.Request, user_id: str) -> httpx.Response:
        import json

        if user_id not in self.users:
            return httpx.Response(404)
        self.users[user_id] = {**self.users[user_id], **json.loads(request.content)}
        return httpx.Response(204)

    def _reset_password(self, request: httpx.Request, user_id: str) -> httpx.Response:
        import json

        if user_id not in self.users:
            return httpx.Response(404)
        credential = json.loads(request.content)
        self.password_resets.append((user_id, credential))
        if credential["temporary"] and "UPDATE_PASSWORD" not in self.users[user_id]["requiredActions"]:
            self.users[user_id]["requiredActions"].append("UPDATE_PASSWORD")  # what Keycloak does itself
        return httpx.Response(204)

    def id_token(
        self,
        *,
        sub: str = "kc-sub-1",
        email: str = "morgan@example.com",
        nonce: str = "",
        name: str = "Morgan Example",
        iss: str = ISSUER,
        aud: str = "smb-app",
        exp_offset: int = 300,
        key: RSAKey | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": iss,
            "aud": aud,
            "sub": sub,
            "email": email,
            "name": name,
            "nonce": nonce,
            "iat": now,
            "exp": now + exp_offset,
        }
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, key or self.key)


@pytest.fixture
def keycloak() -> Iterator[FakeKeycloak]:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        yield FakeKeycloak(router)


@pytest.fixture
def app(registry: Registry) -> Any:
    return create_app(registry)


def https_client(app: Any) -> TestClient:
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def start_login(client: TestClient, return_to: str = "/customers") -> dict[str, str]:
    response = client.get("/api/v1/auth/login", params={"return_to": return_to})
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == [
        "https://smb.test/api/v1/auth/callback"
    ]
    return {"state": query["state"][0], "nonce": query["nonce"][0]}


def make_business(slug: str, timezone: str = "America/Denver") -> uuid.UUID:
    business_id = new_id()
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business_id)
    with transaction(ctx) as db:
        org.create_business(db, ctx, name=slug.title(), slug=slug, timezone=timezone, currency="USD", locale="en-US")
    return business_id
