"""Calls from this install to the payments relay (app/relay), which holds the Stripe platform key.

Every call after registration is signed with this business's relay secret and carries a fresh nonce.
"""

from __future__ import annotations

import json
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from app.platform import clock

from . import signing


class RelayError(Exception):
    """kind: `unavailable` (try again later), `refused` (the relay or Stripe said no), `auth` (bad credential),
    `not_found` (no account yet)."""

    def __init__(self, kind: str, code: str, message: str) -> None:
        super().__init__(message)
        self.kind, self.code, self.message = kind, code, message


@dataclass(frozen=True, slots=True)
class Credentials:
    client_id: str
    secret: str


@dataclass(frozen=True, slots=True)
class Registration:
    client_id: str
    secret: str
    mode: str


@dataclass(frozen=True, slots=True)
class AccountView:
    id: str
    country: str | None
    default_currency: str | None
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    currently_due: list[str]
    past_due: list[str]
    disabled_reason: str | None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> AccountView:
        if not isinstance(data.get("id"), str):
            raise RelayError("unavailable", "bad_response", "The payments relay answered without an account id")
        return cls(
            id=data["id"],
            country=data.get("country"),
            default_currency=data.get("default_currency"),
            charges_enabled=bool(data.get("charges_enabled")),
            payouts_enabled=bool(data.get("payouts_enabled")),
            details_submitted=bool(data.get("details_submitted")),
            currently_due=[str(x) for x in data.get("currently_due") or []],
            past_due=[str(x) for x in data.get("past_due") or []],
            disabled_reason=data.get("disabled_reason"),
        )


class RelayApi:
    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    def _send(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        url = self._base + path
        try:
            response = self._client.request(method, url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise RelayError(
                "unavailable", "unreachable", f"The payments relay could not be reached ({type(exc).__name__})"
            ) from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            code = str(error.get("code") or "error")[:60]
            message = str(error.get("message") or f"The payments relay answered {response.status_code}")[:300]
            if response.status_code == 401:
                raise RelayError("auth", code, message)
            if response.status_code == 404:
                raise RelayError("not_found", code, message)
            if response.status_code in (429, 502, 503, 504) or response.status_code >= 500:
                raise RelayError("unavailable", code, message)
            raise RelayError("refused", code, message)
        if not isinstance(data, dict):
            raise RelayError("unavailable", "bad_response", "The payments relay answered with something unexpected")
        return data

    def _signed(self, creds: Credentials, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        content = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
        nonce = secrets.token_urlsafe(18)
        full_path = urllib.parse.urlsplit(self._base + path).path
        payload = signing.request_payload(method, full_path, nonce, content)
        headers = {
            "Content-Type": "application/json",
            signing.CLIENT_HEADER: creds.client_id,
            signing.NONCE_HEADER: nonce,
            signing.SIGNATURE_HEADER: signing.sign(creds.secret, int(clock.now().timestamp()), payload),
        }
        return self._send(method, path, content, headers)

    def register(
        self, *, name: str, business_ref: str, base_url: str, events_url: str, enrollment_token: str
    ) -> Registration:
        body = {"name": name, "business_ref": business_ref, "base_url": base_url, "events_url": events_url}
        headers = {"Content-Type": "application/json"}
        if enrollment_token:
            headers["Authorization"] = f"Bearer {enrollment_token}"
        data = self._send("POST", "/v1/clients", json.dumps(body).encode(), headers)
        try:
            return Registration(str(data["client_id"]), str(data["secret"]), str(data.get("mode", "test")))
        except KeyError:
            raise RelayError("unavailable", "bad_response", "The payments relay did not return a credential") from None

    def create_account(self, creds: Credentials, *, country: str, email: str | None, name: str) -> AccountView:
        return AccountView.parse(
            self._signed(creds, "POST", "/v1/account", {"country": country, "email": email, "name": name})
        )

    def get_account(self, creds: Credentials) -> AccountView:
        return AccountView.parse(self._signed(creds, "GET", "/v1/account"))

    def onboarding_link(self, creds: Credentials) -> str:
        url = self._signed(creds, "POST", "/v1/account/onboarding_link").get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RelayError("unavailable", "bad_response", "The payments relay did not return an onboarding link")
        return url
