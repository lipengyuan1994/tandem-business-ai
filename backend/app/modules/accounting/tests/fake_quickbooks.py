"""An in-memory QuickBooks Online sandbox behind respx: OAuth token endpoint, revoke, and the v3 API calls the
sync uses. Synthetic data only; no network."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import defaultdict
from typing import Any

import httpx
import respx

from app.modules.accounting.quickbooks import API_BASE, REVOKE_URL, TOKEN_URL

REALM = "9341450000000001"
ROOTS = {
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
ACCOUNTS = [
    {"Id": "1", "Name": "Sales", "AccountType": "Income", "AccountSubType": "SalesOfProductIncome"},
    {"Id": "2", "Name": "Checking", "AccountType": "Bank", "AccountSubType": "Checking"},
    {"Id": "3", "Name": "Supplies", "AccountType": "Expense", "AccountSubType": "SuppliesMaterials"},
    {"Id": "4", "Name": "Sales tax payable", "AccountType": "Other Current Liability"},
]


def fault(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"Fault": {"Error": [{"Message": message, "code": code}], "type": "x"}})


class FakeQuickBooks:
    def __init__(self, router: respx.MockRouter, *, home_currency: str = "USD", country: str = "US") -> None:
        self.objects: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.objects["account"] = {a["Id"]: {**a, "SyncToken": "0", "Active": True} for a in ACCOUNTS}
        self.home_currency, self.country = home_currency, country
        self.next_id = 100
        self.access, self.refresh = "", ""
        self.issued = 0
        self.token_calls: list[dict[str, str]] = []
        self.revoked: list[str] = []
        self.writes: list[tuple[str, str, str]] = []  # (entity, operation, id)
        self.by_request: dict[str, httpx.Response] = {}
        self.fail_next: dict[str, httpx.Response] = {}  # entity -> response for its next create/update
        self.refuse_refresh = False
        router.post(TOKEN_URL).mock(side_effect=self._token)
        router.post(REVOKE_URL).mock(side_effect=self._revoke)
        router.route(host=urllib.parse.urlparse(API_BASE["sandbox"]).hostname).mock(side_effect=self._api)

    # -- OAuth ---------------------------------------------------------------------------------------------

    def _issue(self) -> httpx.Response:
        self.issued += 1
        self.access, self.refresh = f"access-{self.issued}", f"refresh-{self.issued}"
        return httpx.Response(
            200,
            json={
                "access_token": self.access,
                "refresh_token": self.refresh,
                "token_type": "bearer",
                "expires_in": 3600,
                "x_refresh_token_expires_in": 8726400,
            },
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        self.token_calls.append(form)
        if form["grant_type"] == "authorization_code":
            return (
                self._issue()
                if form.get("code") == "good-code"
                else httpx.Response(400, json={"error": "invalid_grant"})
            )
        if self.refuse_refresh or form.get("refresh_token") != self.refresh:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return self._issue()

    def _revoke(self, request: httpx.Request) -> httpx.Response:
        self.revoked.append(json.loads(request.content)["token"])
        return httpx.Response(200)

    # -- API -----------------------------------------------------------------------------------------------

    def of(self, entity: str) -> list[dict[str, Any]]:
        return list(self.objects[entity].values())

    def _api(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.access}":
            return fault(401, "3200", "AuthenticationFailed")
        assert request.url.params.get("minorversion") == "75"
        match = re.fullmatch(rf"/v3/company/{REALM}/(.+)", request.url.path)
        assert match, request.url.path
        path = match.group(1)
        if request.method == "GET":
            return self._get(path, request)
        rid = request.url.params.get("requestid")
        assert rid, "writes must carry requestid"
        if rid in self.by_request:
            return self.by_request[rid]
        response = self._post(path, json.loads(request.content), request.url.params.get("operation"))
        if response.status_code < 429:  # throttled or unavailable requests were never processed
            self.by_request[rid] = response
        return response

    def _get(self, path: str, request: httpx.Request) -> httpx.Response:
        if path == f"companyinfo/{REALM}":
            return httpx.Response(200, json={"CompanyInfo": {"CompanyName": "Example Books", "Country": self.country}})
        if path == "preferences":
            prefs = {"CurrencyPrefs": {"HomeCurrency": {"value": self.home_currency}}}
            return httpx.Response(200, json={"Preferences": prefs})
        if path == "query":
            return self._query(request.url.params["query"])
        entity, _, remote_id = path.partition("/")
        obj = self.objects[entity].get(remote_id)
        if obj is None:
            return fault(400, "610", "Object Not Found")
        return httpx.Response(200, json={ROOTS[entity]: obj})

    def _query(self, statement: str) -> httpx.Response:
        found = re.fullmatch(r"select \* from (\w+) where (\w+) = '((?:[^'\\]|\\.)*)'", statement)
        if found:
            table, field, raw = found.groups()
            value = raw.replace("\\'", "'").replace("\\\\", "\\")
            rows = [o for o in self.of(table.lower()) if o.get(field) == value and not o.get("deleted")]
            return httpx.Response(200, json={"QueryResponse": {table: rows} if rows else {}})
        assert statement.startswith("select * from Account where Active = true"), statement
        return httpx.Response(200, json={"QueryResponse": {"Account": self.of("account")}})

    def _post(self, entity: str, body: dict[str, Any], operation: str | None) -> httpx.Response:
        if entity in self.fail_next and operation is None:
            return self.fail_next.pop(entity)
        store = self.objects[entity]
        if operation in ("void", "delete") or "Id" in body:
            obj = store.get(str(body["Id"]))
            if obj is None:
                return fault(400, "610", "Object Not Found")
            if str(body["SyncToken"]) != obj["SyncToken"]:
                return fault(400, "5010", "Stale Object Error")
            if operation == "delete":
                obj["deleted"] = True
                self.writes.append((entity, "delete", obj["Id"]))
                return httpx.Response(200, json={ROOTS[entity]: {"Id": obj["Id"], "status": "Deleted"}})
            if operation == "void":
                obj["voided"] = True
            else:
                if not body.get("sparse"):
                    obj.clear()
                obj.update({k: v for k, v in body.items() if k not in ("SyncToken", "sparse")})
            obj["SyncToken"] = str(int(body["SyncToken"]) + 1)
            self.writes.append((entity, operation or "update", obj["Id"]))
            return httpx.Response(200, json={ROOTS[entity]: obj})
        if entity in ("customer", "vendor"):
            names = {o["DisplayName"] for kind in ("customer", "vendor") for o in self.of(kind)}
            if body["DisplayName"] in names:
                return fault(400, "6240", "Duplicate Name Exists Error")
        self.next_id += 1
        obj = {**body, "Id": str(self.next_id), "SyncToken": "0"}
        store[obj["Id"]] = obj
        self.writes.append((entity, "create", obj["Id"]))
        return httpx.Response(200, json={ROOTS[entity]: obj})
