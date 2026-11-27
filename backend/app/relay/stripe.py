"""The few Stripe API calls the relay makes, over plain HTTPS (no Stripe SDK).

Stripe's v1 API takes form-encoded bodies with bracketed keys (`controller[fees][payer]=account`). Every call that
creates something carries an idempotency key so a retry after a lost answer never creates it twice. Calls on a
connected account carry the `Stripe-Account` header (direct charges, ADR-022 decision 2).
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx

from . import signing

WEBHOOK_TOLERANCE_SECONDS = 300


class StripeError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    @property
    def retryable(self) -> bool:
        return self.status in (0, 409, 429) or self.status >= 500


def encode_form(data: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f"{prefix}[{key}]" if prefix else str(key), item)
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                walk(f"{prefix}[{index}]", item)
        elif isinstance(value, bool):
            out.append((prefix, "true" if value else "false"))
        else:
            out.append((prefix, str(value)))

    walk("", data)
    return out


class Stripe:
    def __init__(self, client: httpx.Client, secret_key: str, *, base: str, version: str = "") -> None:
        self._client, self._key, self._base, self._version = client, secret_key, base.rstrip("/"), version

    def request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        account: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._key}"}
        if self._version:
            headers["Stripe-Version"] = self._version
        if account:
            headers["Stripe-Account"] = account
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{self._base}{path}"
        try:
            if method == "GET":
                params: list[tuple[str, str | int | float | bool | None]] = list(encode_form(data or {}))
                response = self._client.get(url, params=params, headers=headers)
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                form = urllib.parse.urlencode(encode_form(data or {}))
                response = self._client.request(method, url, content=form, headers=headers)
        except httpx.HTTPError as exc:
            raise StripeError(0, "network", f"Stripe could not be reached ({type(exc).__name__})") from exc
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            error: dict[str, Any] = body.get("error", {}) if isinstance(body, dict) else {}
            code = str(error.get("code") or error.get("type") or "stripe_error")
            raise StripeError(
                response.status_code, code, str(error.get("message") or "Stripe refused the request")[:300]
            )
        if not isinstance(body, dict):
            raise StripeError(502, "bad_response", "Stripe answered with something that is not an object")
        return body

    # -- connected accounts -----------------------------------------------------------------------------------

    def create_account(self, *, country: str, email: str | None, name: str, metadata: dict[str, str], key: str) -> Any:
        """A connected account with Stripe's default controller settings (ADR-022 decision 1, Accounts v1):
        full Stripe Dashboard, Stripe owns payment losses, the account pays Stripe's fees, Stripe collects
        onboarding requirements."""
        params: dict[str, Any] = {
            "country": country,
            "email": email,
            "business_profile": {"name": name},
            "controller": {
                "fees": {"payer": "account"},
                "losses": {"payments": "stripe"},
                "stripe_dashboard": {"type": "full"},
                "requirement_collection": "stripe",
            },
            "metadata": metadata,
        }
        return self.request("POST", "/v1/accounts", params, idempotency_key=key)

    def retrieve_account(self, account: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/accounts/{account}")

    def create_account_link(self, account: str, *, refresh_url: str, return_url: str) -> dict[str, Any]:
        params = {
            "account": account,
            "refresh_url": refresh_url,
            "return_url": return_url,
            "type": "account_onboarding",
        }
        return self.request("POST", "/v1/account_links", params)

    # -- Checkout Sessions (direct charges on the connected account) --------------------------------------------

    def create_checkout_session(
        self,
        account: str,
        *,
        amount: int,
        currency: str,
        name: str,
        reference: str,
        metadata: dict[str, str],
        success_url: str,
        cancel_url: str,
        expires_at: int,
        methods: list[str],
        application_fee: int,
        key: str,
    ) -> dict[str, Any]:
        """One Stripe-hosted payment page for one amount (ADR-022 decision 2). Card entry happens only there."""
        intent: dict[str, Any] = {"metadata": metadata}
        if application_fee > 0:
            intent["application_fee_amount"] = application_fee
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {"currency": currency, "unit_amount": amount, "product_data": {"name": name}},
                }
            ],
            "payment_method_types": methods,
            "client_reference_id": reference,
            "metadata": metadata,
            "payment_intent_data": intent,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": expires_at,
        }
        return self.request("POST", "/v1/checkout/sessions", params, account=account, idempotency_key=key)

    def retrieve_checkout_session(self, account: str, session: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/checkout/sessions/{session}", account=account)

    def expire_checkout_session(self, account: str, session: str) -> dict[str, Any]:
        return self.request("POST", f"/v1/checkout/sessions/{session}/expire", account=account)


def verify_webhook(payload: bytes, header: str, secret: str, now: int) -> bool:
    """Stripe-Signature: `t=<ts>,v1=<hex HMAC-SHA256 of "<ts>.<raw body>">` (same construction as signing.py)."""
    return bool(secret) and signing.verify(secret, header, payload, now)


def account_view(account: dict[str, Any]) -> dict[str, Any]:
    """The part of a Stripe account a Tandem install needs: readiness and what Stripe still asks for."""
    requirements = account.get("requirements") or {}
    return {
        "id": account.get("id"),
        "country": account.get("country"),
        "default_currency": account.get("default_currency"),
        "charges_enabled": bool(account.get("charges_enabled")),
        "payouts_enabled": bool(account.get("payouts_enabled")),
        "details_submitted": bool(account.get("details_submitted")),
        "currently_due": [str(x) for x in requirements.get("currently_due") or []][:50],
        "past_due": [str(x) for x in requirements.get("past_due") or []][:50],
        "disabled_reason": requirements.get("disabled_reason"),
        "current_deadline": requirements.get("current_deadline"),
    }


def checkout_view(session: dict[str, Any]) -> dict[str, Any]:
    """What Tandem needs to record a payment: never card details (Checkout does not return them anyway)."""
    intent = session.get("payment_intent")
    if isinstance(intent, dict):
        intent = intent.get("id")
    metadata = session.get("metadata") or {}
    return {
        "id": session.get("id"),
        "url": session.get("url"),
        "status": session.get("status"),
        "payment_status": session.get("payment_status"),
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
        "payment_intent": intent,
        "expires_at": session.get("expires_at"),
        "client_reference_id": session.get("client_reference_id"),
        "metadata": {str(k): str(v) for k, v in metadata.items() if str(k).startswith("tandem_")},
    }
