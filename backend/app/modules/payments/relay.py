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


@dataclass(frozen=True, slots=True)
class CheckoutView:
    id: str
    url: str | None
    status: str  # open | complete | expired
    payment_status: str  # paid | unpaid | no_payment_required
    amount_total: int
    currency: str
    payment_intent: str | None
    expires_at: int | None
    request_ref: str | None
    invoice_ref: str | None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> CheckoutView:
        session_id, amount = data.get("id"), data.get("amount_total")
        if not isinstance(session_id, str) or not isinstance(amount, int):
            raise RelayError("unavailable", "bad_response", "The payments relay answered without a checkout session")
        raw_metadata = data.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        url = data.get("url")
        return cls(
            id=session_id,
            url=url if isinstance(url, str) and url.startswith("https://") else None,
            status=str(data.get("status") or ""),
            payment_status=str(data.get("payment_status") or ""),
            amount_total=amount,
            currency=str(data.get("currency") or ""),
            payment_intent=data.get("payment_intent") if isinstance(data.get("payment_intent"), str) else None,
            expires_at=data.get("expires_at") if isinstance(data.get("expires_at"), int) else None,
            request_ref=metadata.get("tandem_request"),
            invoice_ref=metadata.get("tandem_invoice"),
        )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True, slots=True)
class RefundView:
    id: str
    amount: int
    currency: str
    status: str  # pending | requires_action | succeeded | failed | canceled
    failure_reason: str | None
    payment_intent: str | None
    created: int | None
    refund_ref: str | None  # Tandem's refund id, for refunds Tandem sent

    @classmethod
    def parse(cls, data: dict[str, Any]) -> RefundView:
        refund_id, amount = data.get("id"), _int(data.get("amount"))
        if not isinstance(refund_id, str) or amount is None:
            raise RelayError("unavailable", "bad_response", "The payments relay answered without a refund")
        raw_metadata = data.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        return cls(
            id=refund_id,
            amount=amount,
            currency=str(data.get("currency") or ""),
            status=str(data.get("status") or ""),
            failure_reason=_str(data.get("failure_reason")),
            payment_intent=_str(data.get("payment_intent")),
            created=_int(data.get("created")),
            refund_ref=_str(metadata.get("tandem_refund")),
        )


@dataclass(frozen=True, slots=True)
class DisputeView:
    id: str
    amount: int
    currency: str
    status: str  # warning_needs_response | needs_response | under_review | won | lost | warning_closed | ...
    reason: str | None
    payment_intent: str | None
    created: int | None
    due_by: int | None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> DisputeView:
        dispute_id, amount = data.get("id"), _int(data.get("amount"))
        if not isinstance(dispute_id, str) or amount is None:
            raise RelayError("unavailable", "bad_response", "The payments relay answered without a dispute")
        return cls(
            id=dispute_id,
            amount=amount,
            currency=str(data.get("currency") or ""),
            status=str(data.get("status") or ""),
            reason=_str(data.get("reason")),
            payment_intent=_str(data.get("payment_intent")),
            created=_int(data.get("created")),
            due_by=_int(data.get("due_by")),
        )


@dataclass(frozen=True)
class FeeView:
    payment_intent: str
    currency: str
    amount: int | None
    fee: int | None  # None until Stripe has a balance transaction for the charge
    net: int | None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> FeeView:
        intent = data.get("payment_intent")
        if not isinstance(intent, str):
            raise RelayError("unavailable", "bad_response", "The payments relay answered without a payment")
        return cls(
            payment_intent=intent,
            currency=str(data.get("currency") or ""),
            amount=_int(data.get("amount")),
            fee=_int(data.get("fee")),
            net=_int(data.get("net")),
        )


@dataclass(frozen=True)
class PayoutView:
    id: str
    amount: int
    currency: str
    status: str  # pending | in_transit | paid | failed | canceled
    arrival_date: int | None
    automatic: bool
    method: str | None
    failure_code: str | None

    @classmethod
    def parse(cls, data: dict[str, Any]) -> PayoutView:
        payout_id, amount = data.get("id"), _int(data.get("amount"))
        if not isinstance(payout_id, str) or amount is None:
            raise RelayError("unavailable", "bad_response", "The payments relay answered without a payout")
        return cls(
            id=payout_id,
            amount=amount,
            currency=str(data.get("currency") or ""),
            status=str(data.get("status") or ""),
            arrival_date=_int(data.get("arrival_date")),
            automatic=bool(data.get("automatic")),
            method=_str(data.get("method")),
            failure_code=_str(data.get("failure_code")),
        )


@dataclass(frozen=True)
class PayoutSummary:
    payout: PayoutView
    available: bool  # False for a manual payout: Stripe does not list what it contains
    complete: bool
    charges: int
    refunds: int
    disputes: int
    other: int
    fees: int
    count: int

    @classmethod
    def parse(cls, data: dict[str, Any]) -> PayoutSummary:
        payout = data.get("payout")
        if not isinstance(payout, dict):
            raise RelayError("unavailable", "bad_response", "The payments relay answered without a payout")
        return cls(
            payout=PayoutView.parse(payout),
            available=bool(data.get("available")),
            complete=bool(data.get("complete")),
            **{k: _int(data.get(k)) or 0 for k in ("charges", "refunds", "disputes", "other", "fees", "count")},
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

    def create_checkout(
        self,
        creds: Credentials,
        *,
        request_id: str,
        invoice_id: str,
        amount: int,
        currency: str,
        description: str,
        success_path: str,
        cancel_path: str,
        expires_at: int,
        bank_transfers: bool,
    ) -> CheckoutView:
        body = {
            "request_id": request_id,
            "invoice_ref": invoice_id,
            "amount": amount,
            "currency": currency.lower(),
            "description": description,
            "success_path": success_path,
            "cancel_path": cancel_path,
            "expires_at": expires_at,
            "bank_transfers": bank_transfers,
        }
        return CheckoutView.parse(self._signed(creds, "POST", "/v1/checkout_sessions", body))

    def get_checkout(self, creds: Credentials, session_id: str) -> CheckoutView:
        return CheckoutView.parse(self._signed(creds, "GET", f"/v1/checkout_sessions/{session_id}"))

    def expire_checkout(self, creds: Credentials, session_id: str) -> CheckoutView:
        return CheckoutView.parse(self._signed(creds, "POST", f"/v1/checkout_sessions/{session_id}/expire"))

    def create_refund(
        self, creds: Credentials, *, refund_id: str, invoice_id: str, payment_intent: str, amount: int
    ) -> RefundView:
        body = {"refund_ref": refund_id, "invoice_ref": invoice_id, "payment_intent": payment_intent, "amount": amount}
        return RefundView.parse(self._signed(creds, "POST", "/v1/refunds", body))

    def get_refund(self, creds: Credentials, refund_id: str) -> RefundView:
        return RefundView.parse(self._signed(creds, "GET", f"/v1/refunds/{refund_id}"))

    def get_dispute(self, creds: Credentials, dispute_id: str) -> DisputeView:
        return DisputeView.parse(self._signed(creds, "GET", f"/v1/disputes/{dispute_id}"))

    def get_payment_fee(self, creds: Credentials, payment_intent: str) -> FeeView:
        return FeeView.parse(self._signed(creds, "GET", f"/v1/payments/{payment_intent}/fee"))

    def get_payout(self, creds: Credentials, payout_id: str) -> PayoutView:
        return PayoutView.parse(self._signed(creds, "GET", f"/v1/payouts/{payout_id}"))

    def get_payout_summary(self, creds: Credentials, payout_id: str) -> PayoutSummary:
        return PayoutSummary.parse(self._signed(creds, "GET", f"/v1/payouts/{payout_id}/summary"))
