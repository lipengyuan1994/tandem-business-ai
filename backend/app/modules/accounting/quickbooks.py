"""QuickBooks Online client: OAuth 2.0 (authorization code) and the v3 Accounting API, over platform HTTP.

Everything bookkeeping-specific lives behind `LedgerApi` / `LedgerAuth` so tests (and a later Xero client) can
swap the implementation. Requests go through `app.platform.http.client()`, so Intuit's hosts must be listed in
EGRESS_ALLOWLIST. Never log tokens, authorization codes or request bodies (they hold customer names).

Endpoints and fields follow Intuit's QuickBooks Online Accounting API v3 (minor version 75, the minimum Intuit
accepts since August 2025). Writes carry `requestid`, which QuickBooks uses to answer a repeated request with the
original result instead of creating a second object.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

import httpx

from app.platform import clock
from app.platform.http import EgressDenied

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"  # noqa: S105 - an endpoint, not a secret
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
API_BASE = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
SCOPE = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "75"
INTUIT_HOSTS = frozenset(
    {
        "oauth.platform.intuit.com",
        "developer.api.intuit.com",
        "sandbox-quickbooks.api.intuit.com",
        "quickbooks.api.intuit.com",
    }
)

ErrorKind = Literal["auth", "throttled", "validation", "duplicate_name", "stale", "not_found", "unavailable", "config"]


class LedgerError(Exception):
    """A failed bookkeeping call. `message` is safe to show staff and store (no tokens, no request bodies)."""

    def __init__(self, kind: ErrorKind, message: str, code: str | None = None) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message[:400]
        self.code = code

    @property
    def stops_run(self) -> bool:
        """Errors that will fail every following call too, so the sync run stops and tries again later."""
        return self.kind in ("auth", "throttled", "unavailable", "config")


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class RemoteRef:
    id: str
    sync_token: str | None


class LedgerAuth(Protocol):
    def authorize_url(self, state: str) -> str: ...
    def exchange_code(self, code: str) -> Tokens: ...
    def refresh(self, refresh_token: str) -> Tokens: ...
    def revoke(self, token: str) -> None: ...


class LedgerApi(Protocol):
    def company(self) -> dict[str, str | None]: ...
    def query(self, statement: str) -> list[dict[str, Any]]: ...
    def read(self, entity: str, remote_id: str) -> dict[str, Any]: ...
    def create(self, entity: str, body: dict[str, Any], request_id: str) -> RemoteRef: ...
    def update(self, entity: str, body: dict[str, Any], request_id: str) -> RemoteRef: ...
    def void(self, entity: str, remote_id: str, sync_token: str, request_id: str) -> RemoteRef: ...
    def delete(self, entity: str, remote_id: str, sync_token: str, request_id: str) -> None: ...


# Path segment -> JSON root object, for the entities the sync writes or reads.
ENTITIES = {
    "customer": "Customer",
    "vendor": "Vendor",
    "item": "Item",
    "account": "Account",
    "invoice": "Invoice",
    "creditmemo": "CreditMemo",
    "payment": "Payment",
    "bill": "Bill",
    "billpayment": "BillPayment",
}


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)  # amounts have at most 3 decimals; float repr round-trips them exactly
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _dump(body: dict[str, Any]) -> bytes:
    return json.dumps(body, default=_json_default, separators=(",", ":")).encode()


def _transport_error(exc: Exception) -> LedgerError:
    if isinstance(exc, EgressDenied):
        return LedgerError("config", "QuickBooks hosts are not in EGRESS_ALLOWLIST on this server")
    if isinstance(exc, httpx.TimeoutException):
        return LedgerError("unavailable", "QuickBooks did not answer in time")
    return LedgerError("unavailable", "Could not reach QuickBooks")


def _fault(response: httpx.Response) -> LedgerError:
    """Map an Intuit error response (`{"Fault": {"Error": [{"Message", "Detail", "code"}], "type"}}`)."""
    status = response.status_code
    code: str | None = None
    message = f"QuickBooks answered {status}"
    try:
        data = response.json()
    except ValueError:
        data = {}
    fault = data.get("Fault") or data.get("fault") or {}
    errors = fault.get("Error") or fault.get("error") or []
    if errors:
        first = errors[0]
        code = str(first.get("code") or "") or None
        message = str(first.get("Message") or first.get("message") or message)
        detail = str(first.get("Detail") or first.get("detail") or "")
        if detail and detail != message:
            message = f"{message}: {detail}"
    if status == 401 or code in ("3200", "3201", "100"):
        return LedgerError("auth", "QuickBooks no longer accepts this connection; reconnect it", code)
    if status == 429 or code == "003001":
        return LedgerError("throttled", "QuickBooks is rate limiting requests; the sync will continue later", code)
    if status >= 500:
        return LedgerError("unavailable", message, code)
    if code == "6240":
        return LedgerError("duplicate_name", message, code)
    if code == "5010":
        return LedgerError("stale", message, code)
    if code == "610" or status == 404:
        return LedgerError("not_found", message, code)
    return LedgerError("validation", message, code)


class QuickBooksAuth:
    def __init__(self, http: httpx.Client, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._http, self._id, self._secret, self._redirect = http, client_id, client_secret, redirect_uri

    def authorize_url(self, state: str) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self._id,
                "response_type": "code",
                "scope": SCOPE,
                "redirect_uri": self._redirect,
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def _token_call(self, form: dict[str, str]) -> Tokens:
        try:
            response = self._http.post(
                TOKEN_URL, data=form, auth=(self._id, self._secret), headers={"Accept": "application/json"}
            )
        except (httpx.HTTPError, EgressDenied) as exc:
            raise _transport_error(exc) from exc
        if response.status_code >= 500:
            raise LedgerError("unavailable", f"QuickBooks sign-in answered {response.status_code}")
        if response.status_code != 200:
            try:
                reason = str(response.json().get("error") or "")
            except ValueError:
                reason = ""
            if reason == "invalid_client":
                raise LedgerError("config", "QuickBooks rejected this server's client id or secret")
            raise LedgerError("auth", "QuickBooks did not accept the authorization; connect again", reason or None)
        data = response.json()
        now = clock.now()
        refresh_ttl = data.get("x_refresh_token_expires_in")
        return Tokens(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            access_expires_at=now + timedelta(seconds=int(data.get("expires_in") or 3600)),
            refresh_expires_at=now + timedelta(seconds=int(refresh_ttl)) if refresh_ttl else None,
        )

    def exchange_code(self, code: str) -> Tokens:
        return self._token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": self._redirect})

    def refresh(self, refresh_token: str) -> Tokens:
        return self._token_call({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def revoke(self, token: str) -> None:
        try:
            response = self._http.post(
                REVOKE_URL,
                content=_dump({"token": token}),
                auth=(self._id, self._secret),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        except (httpx.HTTPError, EgressDenied) as exc:
            raise _transport_error(exc) from exc
        if response.status_code not in (200, 204):
            raise LedgerError("unavailable", f"QuickBooks did not confirm the revoke ({response.status_code})")


class QuickBooksApi:
    def __init__(self, http: httpx.Client, environment: str, realm_id: str, access_token: str) -> None:
        self._http = http
        self._base = f"{API_BASE[environment]}/v3/company/{urllib.parse.quote(realm_id, safe='')}"
        self._realm = realm_id
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _call(
        self, method: str, path: str, params: dict[str, str] | None = None, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        query = {"minorversion": MINOR_VERSION, **(params or {})}
        try:
            response = self._http.request(
                method,
                f"{self._base}/{path}",
                params=query,
                content=_dump(body) if body is not None else None,
                headers=self._headers,
            )
        except (httpx.HTTPError, EgressDenied) as exc:
            raise _transport_error(exc) from exc
        if response.status_code != 200:
            raise _fault(response)
        data = response.json()
        if isinstance(data, dict) and data.get("Fault"):
            raise _fault(response)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _ref(entity: str, data: dict[str, Any]) -> RemoteRef:
        obj = data.get(ENTITIES[entity]) or {}
        if not obj.get("Id"):
            raise LedgerError("validation", "QuickBooks returned no id for the saved record")
        token = obj.get("SyncToken")
        return RemoteRef(id=str(obj["Id"]), sync_token=str(token) if token is not None else None)

    def company(self) -> dict[str, str | None]:
        info = self._call("GET", f"companyinfo/{urllib.parse.quote(self._realm, safe='')}").get("CompanyInfo") or {}
        prefs = self._call("GET", "preferences").get("Preferences") or {}
        currency = ((prefs.get("CurrencyPrefs") or {}).get("HomeCurrency") or {}).get("value")
        return {
            "company_name": info.get("CompanyName"),
            "country": info.get("Country"),
            "home_currency": currency,
        }

    def query(self, statement: str) -> list[dict[str, Any]]:
        response = self._call("GET", "query", params={"query": statement}).get("QueryResponse") or {}
        for value in response.values():
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []

    def read(self, entity: str, remote_id: str) -> dict[str, Any]:
        data = self._call("GET", f"{entity}/{urllib.parse.quote(remote_id, safe='')}")
        obj = data.get(ENTITIES[entity])
        if not isinstance(obj, dict):
            raise LedgerError("not_found", f"QuickBooks has no {ENTITIES[entity]} {remote_id}")
        return obj

    def create(self, entity: str, body: dict[str, Any], request_id: str) -> RemoteRef:
        return self._ref(entity, self._call("POST", entity, params={"requestid": request_id}, body=body))

    def update(self, entity: str, body: dict[str, Any], request_id: str) -> RemoteRef:
        return self._ref(entity, self._call("POST", entity, params={"requestid": request_id}, body=body))

    def void(self, entity: str, remote_id: str, sync_token: str, request_id: str) -> RemoteRef:
        body = {"Id": remote_id, "SyncToken": sync_token, "sparse": True}
        data = self._call("POST", entity, params={"operation": "void", "requestid": request_id}, body=body)
        return self._ref(entity, data)

    def delete(self, entity: str, remote_id: str, sync_token: str, request_id: str) -> None:
        body = {"Id": remote_id, "SyncToken": sync_token}
        self._call("POST", entity, params={"operation": "delete", "requestid": request_id}, body=body)


def quote_literal(value: str) -> str:
    """A string literal for the QuickBooks query language (single quotes escaped with a backslash)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def select_where(entity: str, field: str, value: str) -> str:
    """`select * from <Entity> where <Field> = '<value>'` for Intuit's query endpoint (not SQL; value escaped)."""
    if not (entity.isalpha() and field.isalpha()):
        raise ValueError("entity and field must be plain names")
    return f"select * from {entity} where {field} = {quote_literal(value)}"  # noqa: S608
