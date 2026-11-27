"""Test helpers: an in-memory Stripe behind respx, and routing of HTTP calls into an ASGI app's TestClient.

Synthetic data only; no network. Used by the relay's own tests and by app.modules.payments tests.
"""

from __future__ import annotations

import itertools
import json
import secrets
import urllib.parse
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from . import signing

STRIPE_BASE = "https://api.stripe.com"
STRIPE_KEY = "sk_test_example_platform_key"
WEBHOOK_SECRET = "whsec_example_secret"  # noqa: S105  # test-only fake


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(request.content.decode()))


class FakeStripe:
    """Accounts, account links and webhook events of a Connect platform in test mode."""

    def __init__(self, router: respx.MockRouter, *, key: str = STRIPE_KEY) -> None:
        self.key = key
        self.accounts: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []  # method, path, form, headers
        self.by_key: dict[str, httpx.Response] = {}
        self.fail_next: list[httpx.Response] = []
        self._ids = itertools.count(1)
        router.route(host=urllib.parse.urlsplit(STRIPE_BASE).hostname).mock(side_effect=self._handle)

    # -- state helpers used by tests ------------------------------------------------------------------------

    def complete_onboarding(self, account: str) -> None:
        self.accounts[account].update(details_submitted=True, charges_enabled=True, payouts_enabled=True)
        self.accounts[account]["requirements"] = {"currently_due": [], "past_due": [], "disabled_reason": None}

    def restrict(self, account: str, due: list[str]) -> None:
        self.accounts[account].update(charges_enabled=False, payouts_enabled=False, details_submitted=True)
        self.accounts[account]["requirements"] = {
            "currently_due": due,
            "past_due": due,
            "disabled_reason": "requirements.past_due",
        }

    def event(
        self, account: str | None, kind: str, obj: dict[str, Any], *, event_id: str | None = None
    ) -> dict[str, Any]:
        return {
            "id": event_id or f"evt_{secrets.token_hex(8)}",
            "object": "event",
            "type": kind,
            "account": account,
            "created": 1795000000,
            "livemode": False,
            "data": {"object": obj},
        }

    @staticmethod
    def signed(event: dict[str, Any], now: int, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
        body = json.dumps(event).encode()
        return body, signing.sign(secret, now, body)

    # -- the API ------------------------------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        form = _form(request) if request.method != "GET" else dict(request.url.params)
        self.calls.append((request.method, request.url.path, form, dict(request.headers)))
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"type": "invalid_request_error", "message": "Invalid API Key"}})
        if self.fail_next:
            return self.fail_next.pop(0)
        key = request.headers.get("idempotency-key")
        if key and key in self.by_key:
            return self.by_key[key]
        response = self._route(request, form)
        if key and response.status_code < 500:
            self.by_key[key] = response
        return response

    def _route(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/accounts":
            account_id = f"acct_test{next(self._ids):06d}"
            self.accounts[account_id] = {
                "id": account_id,
                "object": "account",
                "country": form.get("country"),
                "email": form.get("email"),
                "default_currency": {"US": "usd", "CA": "cad", "GB": "gbp"}.get(form.get("country", ""), "usd"),
                "charges_enabled": False,
                "payouts_enabled": False,
                "details_submitted": False,
                "requirements": {
                    "currently_due": ["business_type", "external_account", "tos_acceptance.date"],
                    "past_due": [],
                    "disabled_reason": "requirements.past_due",
                },
                "controller": {
                    "fees": {"payer": form.get("controller[fees][payer]")},
                    "losses": {"payments": form.get("controller[losses][payments]")},
                    "stripe_dashboard": {"type": form.get("controller[stripe_dashboard][type]")},
                    "requirement_collection": form.get("controller[requirement_collection]"),
                },
                "metadata": {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")},
            }
            return httpx.Response(200, json=self.accounts[account_id])
        if request.method == "GET" and path.startswith("/v1/accounts/"):
            account = self.accounts.get(path.rsplit("/", 1)[1])
            if account is None:
                return httpx.Response(404, json={"error": {"code": "resource_missing", "message": "No such account"}})
            return httpx.Response(200, json=account)
        if request.method == "POST" and path == "/v1/account_links":
            if form.get("account") not in self.accounts:
                return httpx.Response(400, json={"error": {"code": "resource_missing", "message": "No such account"}})
            link = {
                "object": "account_link",
                "url": f"https://connect.stripe.com/setup/s/{secrets.token_hex(6)}",
                "expires_at": 1795000300,
                **{k: form[k] for k in ("account", "refresh_url", "return_url", "type")},
            }
            self.links.append(link)
            return httpx.Response(200, json=link)
        return httpx.Response(404, json={"error": {"type": "invalid_request_error", "message": f"no route {path}"}})


def asgi_route(router: respx.MockRouter, host: str, client: TestClient) -> None:
    """Send every respx-intercepted call for `host` into `client` (a TestClient of another ASGI app)."""

    def forward(request: httpx.Request) -> httpx.Response:
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
        )
        return httpx.Response(response.status_code, headers=dict(response.headers), content=response.content)

    router.route(host=host).mock(side_effect=forward)
