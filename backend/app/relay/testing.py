"""Test helpers: an in-memory Stripe behind respx, and routing of HTTP calls into an ASGI app's TestClient.

Synthetic data only; no network. Used by the relay's own tests and by app.modules.payments tests.
"""

from __future__ import annotations

import itertools
import json
import secrets
import time
import urllib.parse
from collections.abc import Callable
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
    """Accounts, account links, Checkout, refunds, disputes and webhook events of a Connect platform in test mode."""

    def __init__(self, router: respx.MockRouter, *, key: str = STRIPE_KEY) -> None:
        self.key = key
        self.accounts: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self.sessions: dict[str, dict[str, Any]] = {}
        self.intents: dict[str, dict[str, Any]] = {}  # payment intent id -> account, amount, currency
        self.refunds: dict[str, dict[str, Any]] = {}
        self.disputes: dict[str, dict[str, Any]] = {}
        self.refund_status = "succeeded"  # what a new refund starts as ("pending" for bank debits and slow cards)
        self.balance: list[dict[str, Any]] = []  # balance transactions, in order
        self.payouts: dict[str, dict[str, Any]] = {}
        self.page_size = 100  # largest page of balance transactions served
        self.fee_fixed = 30  # Stripe's card fee in this fake: 2.9% + 30 (minor units)
        self.calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []  # method, path, form, headers
        self.by_key: dict[str, httpx.Response] = {}
        self.fail_next: list[httpx.Response] = []
        self._ids = itertools.count(1)
        self.clock: Callable[[], int] = lambda: int(time.time())  # when charges happen (tests set the app's clock)
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

    def _intent(self, session: dict[str, Any]) -> str:
        intent = f"pi_{secrets.token_hex(8)}"
        self.intents[intent] = {
            "account": session["account"],
            "amount": session["amount_total"],
            "currency": session["currency"],
        }
        return intent

    def _txn(self, account: str, kind: str, category: str, amount: int, fee: int, source: str) -> dict[str, Any]:
        txn = {
            "id": f"txn_{secrets.token_hex(10)}",
            "object": "balance_transaction",
            "account": account,
            "type": kind,
            "reporting_category": category,
            "amount": amount,
            "fee": fee,
            "net": amount - fee,
            "currency": "usd",
            "source": source,
            "payout": None,
        }
        self.balance.append(txn)
        return txn

    def _charge(self, intent: str) -> None:
        """The money arrived: a charge and its balance transaction, with Stripe's fee taken off."""
        found = self.intents[intent]
        fee = round(found["amount"] * 0.029) + self.fee_fixed
        txn = self._txn(found["account"], "charge", "charge", found["amount"], fee, f"ch_{intent[3:]}")
        txn["currency"] = found["currency"]
        found.update(charge=f"ch_{intent[3:]}", balance_transaction=txn, charged_at=self.clock())

    def pay(self, session_id: str) -> dict[str, Any]:
        """The customer paid by card on the Checkout page: returns the session as Stripe sends it in the event."""
        session = self.sessions[session_id]
        session.update(status="complete", payment_status="paid", payment_intent=self._intent(session))
        self._charge(session["payment_intent"])
        return dict(session)

    def payout(self, account: str, *, automatic: bool = True, status: str = "paid") -> str:
        """Stripe pays the account's available balance out to its bank: every transaction not yet paid out."""
        payout_id = f"po_{secrets.token_hex(12)}"
        swept = [t for t in self.balance if t["account"] == account and t["payout"] is None and t["type"] != "payout"]
        amount = sum(t["net"] for t in swept)
        for txn in swept:
            txn["payout"] = payout_id if automatic else None
        own = self._txn(account, "payout", "payout", -amount, 0, payout_id)
        own["payout"] = payout_id
        self.payouts[payout_id] = {
            "id": payout_id,
            "object": "payout",
            "account": account,
            "amount": amount,
            "currency": "usd",
            "status": status,
            "arrival_date": 1795910400,  # 2026-11-29 UTC
            "created": 1795791600,  # 2026-11-27 15:00 UTC
            "automatic": automatic,
            "method": "standard",
            "failure_code": None,
        }
        return payout_id

    def fail_payout(self, payout_id: str, code: str = "account_closed") -> None:
        self.payouts[payout_id].update(status="failed", failure_code=code)

    def start_bank_payment(self, session_id: str) -> dict[str, Any]:
        """The customer chose a bank debit: Checkout completes, the money arrives days later (or never)."""
        session = self.sessions[session_id]
        session.update(status="complete", payment_status="unpaid", payment_intent=self._intent(session))
        return dict(session)

    def refunded(self, intent: str) -> int:
        live = (r for r in self.refunds.values() if r["payment_intent"] == intent and r["status"] != "failed")
        return sum(r["amount"] for r in live)

    def _new_refund(self, intent: str, amount: int, metadata: dict[str, str]) -> dict[str, Any]:
        refund_id = f"re_{secrets.token_hex(12)}"
        self.refunds[refund_id] = {
            "id": refund_id,
            "object": "refund",
            "account": self.intents[intent]["account"],
            "amount": amount,
            "currency": self.intents[intent]["currency"],
            "payment_intent": intent,
            "charge": f"ch_{intent[3:]}",
            "status": self.refund_status,
            "failure_reason": None,
            "reason": "requested_by_customer",
            "created": 1795000100,
            "metadata": metadata,
        }
        self._txn(self.intents[intent]["account"], "refund", "refund", -amount, 0, refund_id)
        return self.refunds[refund_id]

    def dashboard_refund(self, intent: str, amount: int) -> str:
        """Someone at the business refunded part of a payment in their Stripe Dashboard (no Tandem metadata)."""
        return str(self._new_refund(intent, amount, {})["id"])

    def finish_refund(self, refund_id: str, *, ok: bool, reason: str = "expired_or_canceled_card") -> None:
        self.refunds[refund_id].update(status="succeeded" if ok else "failed", failure_reason=None if ok else reason)

    def dispute(self, intent: str, amount: int | None = None, *, reason: str = "fraudulent") -> str:
        """The cardholder disputed a payment with their bank."""
        dispute_id = f"dp_{secrets.token_hex(12)}"
        self.disputes[dispute_id] = {
            "id": dispute_id,
            "object": "dispute",
            "account": self.intents[intent]["account"],
            "amount": amount if amount is not None else self.intents[intent]["amount"] - self.refunded(intent),
            "currency": self.intents[intent]["currency"],
            "payment_intent": intent,
            "charge": f"ch_{intent[3:]}",
            "status": "needs_response",
            "reason": reason,
            "created": 1795000200,
            "evidence_details": {"due_by": 1795800000, "has_evidence": False, "submission_count": 0},
        }
        found = self.disputes[dispute_id]
        self._txn(found["account"], "adjustment", "dispute", -found["amount"], 1500, dispute_id)
        return dispute_id

    def close_dispute(self, dispute_id: str, *, won: bool) -> None:
        found = self.disputes[dispute_id]
        found["status"] = "won" if won else "lost"
        if won:
            self._txn(found["account"], "adjustment", "dispute_reversal", found["amount"], 0, dispute_id)

    def finish_bank_payment(self, session_id: str, *, ok: bool) -> dict[str, Any]:
        session = self.sessions[session_id]
        if ok:
            session["payment_status"] = "paid"
            self._charge(session["payment_intent"])
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
        if path == "/v1/refunds" or path.startswith(("/v1/refunds/", "/v1/disputes/")):
            return self._refunds(request, form)
        if request.method == "GET" and (
            path == "/v1/balance_transactions" or path.startswith(("/v1/payment_intents/", "/v1/payouts/"))
        ):
            return self._money(request, form)
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
        out = self._public(session)
        intent = session["payment_intent"]
        if intent and "payment_intent.latest_charge" in form.values() and "charge" in self.intents[intent]:
            found = self.intents[intent]
            charge = {"id": found["charge"], "object": "charge", "created": found["charged_at"]}
            out["payment_intent"] = {"id": intent, "object": "payment_intent", "latest_charge": charge}
        return httpx.Response(200, json=out)

    def _refunds(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        account = request.headers.get("stripe-account")
        if account not in self.accounts:
            return httpx.Response(400, json={"error": {"code": "account_invalid", "message": "No such account"}})
        parts = request.url.path.split("/")
        if request.method == "POST" and parts[2] == "refunds" and len(parts) == 3:
            intent = form.get("payment_intent", "")
            if self.intents.get(intent, {}).get("account") != account:
                return httpx.Response(
                    404, json={"error": {"code": "resource_missing", "message": "No such payment_intent"}}
                )
            amount = int(form["amount"])
            if amount > self.intents[intent]["amount"] - self.refunded(intent):
                return httpx.Response(
                    400,
                    json={"error": {"code": "amount_too_large", "message": "Refund is larger than what is left"}},
                )
            metadata = {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")}
            refund = self._new_refund(intent, amount, metadata)
            refund["refund_application_fee"] = form.get("refund_application_fee")
            return httpx.Response(200, json=self._strip(refund))
        if request.method == "GET" and len(parts) == 4:
            store = self.refunds if parts[2] == "refunds" else self.disputes
            found = store.get(parts[3])
            if found is not None and found["account"] == account:
                return httpx.Response(200, json=self._strip(found))
        return httpx.Response(404, json={"error": {"code": "resource_missing", "message": "No such object"}})

    def _money(self, request: httpx.Request, params: dict[str, str]) -> httpx.Response:
        account = request.headers.get("stripe-account")
        missing = httpx.Response(404, json={"error": {"code": "resource_missing", "message": "No such object"}})
        if account not in self.accounts:
            return httpx.Response(400, json={"error": {"code": "account_invalid", "message": "No such account"}})
        parts = request.url.path.split("/")
        if parts[2] == "payment_intents" and len(parts) == 4:
            found = self.intents.get(parts[3])
            if found is None or found["account"] != account:
                return missing
            txn = found.get("balance_transaction")
            expanded = any(
                k.startswith("expand") and v == "latest_charge.balance_transaction" for k, v in params.items()
            )
            charge: Any = found.get("charge")
            if charge and expanded:
                charge = {"id": charge, "object": "charge", "balance_transaction": self._strip(txn) if txn else None}
            return httpx.Response(
                200,
                json={
                    "id": parts[3],
                    "object": "payment_intent",
                    "amount": found["amount"],
                    "amount_received": found["amount"] if found.get("charge") else 0,
                    "currency": found["currency"],
                    "status": "succeeded" if found.get("charge") else "processing",
                    "latest_charge": charge,
                },
            )
        if parts[2] == "payouts" and len(parts) == 4:
            payout = self.payouts.get(parts[3])
            return (
                missing
                if payout is None or payout["account"] != account
                else httpx.Response(200, json=self._strip(payout))
            )
        if parts[2] == "balance_transactions" and len(parts) == 3:
            payout = self.payouts.get(params.get("payout", ""))
            if payout is None or payout["account"] != account:
                return missing
            if not payout["automatic"]:
                return httpx.Response(
                    400,
                    json={
                        "error": {"code": "invalid_request_error", "message": "Only automatic payouts can be listed"}
                    },
                )
            rows = [t for t in self.balance if t["account"] == account and t["payout"] == payout["id"]]
            after = params.get("starting_after")
            if after:
                ids = [t["id"] for t in rows]
                rows = rows[ids.index(after) + 1 :] if after in ids else []
            size = min(int(params.get("limit", "10")), self.page_size)
            return httpx.Response(
                200,
                json={"object": "list", "data": [self._strip(t) for t in rows[:size]], "has_more": len(rows) > size},
            )
        return missing

    @staticmethod
    def _strip(obj: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in obj.items() if k not in ("account", "refund_application_fee")}

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
