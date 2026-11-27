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
        self.sessions: dict[str, dict[str, Any]] = {}
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

    def pay(self, session_id: str) -> dict[str, Any]:
        """The customer paid by card on the Checkout page: returns the session as Stripe sends it in the event."""
        session = self.sessions[session_id]
        session.update(status="complete", payment_status="paid", payment_intent=f"pi_{secrets.token_hex(8)}")
        return dict(session)

    def start_bank_payment(self, session_id: str) -> dict[str, Any]:
        """The customer chose a bank debit: Checkout completes, the money arrives days later (or never)."""
        session = self.sessions[session_id]
        session.update(status="complete", payment_status="unpaid", payment_intent=f"pi_{secrets.token_hex(8)}")
        return dict(session)

    def finish_bank_payment(self, session_id: str, *, ok: bool) -> dict[str, Any]:
        session = self.sessions[session_id]
        if ok:
            session["payment_status"] = "paid"
        return dict(session)

    def expire_session(self, session_id: str) -> dict[str, Any]:
        self.sessions[session_id]["status"] = "expired"
        return dict(self.sessions[session_id])

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
        if path == "/v1/checkout/sessions" or path.startswith("/v1/checkout/sessions/"):
            return self._checkout(request, form)
        return httpx.Response(404, json={"error": {"type": "invalid_request_error", "message": f"no route {path}"}})

    def _checkout(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        account = request.headers.get("stripe-account")
        missing = httpx.Response(404, json={"error": {"code": "resource_missing", "message": "No such session"}})
        if account not in self.accounts:
            return httpx.Response(400, json={"error": {"code": "account_invalid", "message": "No such account"}})
        parts = request.url.path.split("/")
        if request.method == "POST" and len(parts) == 4:
            if not self.accounts[account]["charges_enabled"]:
                return httpx.Response(
                    400, json={"error": {"code": "account_invalid", "message": "This account cannot take payments"}}
                )
            session_id = f"cs_test_{secrets.token_hex(12)}"
            self.sessions[session_id] = {
                "id": session_id,
                "object": "checkout.session",
                "account": account,
                "url": f"https://checkout.stripe.com/c/pay/{session_id}",
                "status": "open",
                "payment_status": "unpaid",
                "mode": form.get("mode"),
                "amount_total": int(form["line_items[0][price_data][unit_amount]"]),
                "currency": form.get("line_items[0][price_data][currency]"),
                "payment_intent": None,
                "expires_at": int(form["expires_at"]),
                "client_reference_id": form.get("client_reference_id"),
                "success_url": form.get("success_url"),
                "cancel_url": form.get("cancel_url"),
                "payment_method_types": [v for k, v in sorted(form.items()) if k.startswith("payment_method_types[")],
                "application_fee_amount": form.get("payment_intent_data[application_fee_amount]"),
                "metadata": {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")},
            }
            return httpx.Response(200, json=self._public(self.sessions[session_id]))
        session = self.sessions.get(parts[4]) if len(parts) > 4 else None
        if session is None or session["account"] != account:
            return missing
        if request.method == "POST" and len(parts) == 6 and parts[5] == "expire":
            if session["status"] != "open":
                return httpx.Response(
                    400, json={"error": {"code": "checkout_session_not_open", "message": "Session is not open"}}
                )
            session["status"] = "expired"
        elif not (request.method == "GET" and len(parts) == 5):
            return missing
        return httpx.Response(200, json=self._public(session))

    @staticmethod
    def _public(session: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in session.items() if k not in ("account", "application_fee_amount")}
        if out["status"] != "open":
            out["url"] = None
        return out


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
