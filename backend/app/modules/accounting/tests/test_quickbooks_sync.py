"""QuickBooks Online connection and sync against an in-memory QuickBooks (respx); synthetic data only."""

from __future__ import annotations

import urllib.parse
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from app.modules.accounting import payloads
from app.modules.accounting.quickbooks import LedgerError, _fault, quote_literal, select_where
from app.modules.identity.testing import login_as
from app.modules.sales.public import LedgerInvoice, LedgerLine
from app.platform import clock
from app.platform.config import get_settings
from app.platform.db import transaction
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for

from .fake_quickbooks import REALM, FakeQuickBooks, fault
from .helpers import BASE, QB, Shop, connect, issued_invoice, map_accounts, pay, records, supplier_bill, sync

# Shop.create(): 2 x brake service at 45.00, no tax -> 90.00


def test_connect_stores_sealed_tokens_and_company(shop: Shop, intuit: FakeQuickBooks, owner_engine: Engine) -> None:
    r = shop.owner.post(f"{QB}/connect")
    assert r.status_code == 200, r.text
    url = urllib.parse.urlparse(r.json()["authorize_url"])
    query = urllib.parse.parse_qs(url.query)
    assert (url.netloc, query["client_id"], query["scope"]) == (
        "appcenter.intuit.com",
        ["example-client"],
        ["com.intuit.quickbooks.accounting"],
    )
    assert query["redirect_uri"] == [get_settings().app_base_url + "/api/v1/accounting/quickbooks/callback"]
    state = query["state"][0]

    r = shop.owner.get(
        f"{QB}/callback", params={"code": "good-code", "state": state, "realmId": REALM}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/settings/accounting?quickbooks=connected")
    status = shop.owner.get(QB).json()
    assert (status["status"], status["company_name"], status["home_currency"], status["country"]) == (
        "connected",
        "Example Books",
        "USD",
        "US",
    )
    assert status["sync_from"] == "2026-10-28"
    assert status["missing_mapping"] == ["income_account_id", "expense_account_id", "bank_account_id"]
    with owner_engine.connect() as conn:
        access, refresh, state_hash = conn.execute(
            text("select access_token_sealed, refresh_token_sealed, oauth_state_hash from accounting_connections")
        ).one()
    assert "access-1" not in access and "refresh-1" not in refresh and state_hash is None

    # the state is single use
    r = shop.owner.get(
        f"{QB}/callback", params={"code": "good-code", "state": state, "realmId": REALM}, follow_redirects=False
    )
    assert r.headers["location"].endswith("quickbooks=error&reason=accounting.oauth_state_invalid")


def test_callback_rejects_wrong_state_declines_and_bad_codes(shop: Shop, intuit: FakeQuickBooks) -> None:
    shop.owner.post(f"{QB}/connect")
    r = shop.owner.get(
        f"{QB}/callback", params={"code": "good-code", "state": "forged", "realmId": REALM}, follow_redirects=False
    )
    assert "reason=accounting.oauth_state_invalid" in r.headers["location"]
    r = shop.owner.get(f"{QB}/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "reason=accounting.oauth_declined" in r.headers["location"]
    r = connect(shop, code="bad-code")
    assert "reason=accounting.quickbooks_auth" in r.headers["location"]
    assert shop.owner.get(QB).json()["status"] == "pending"


def test_connect_needs_config_owner_and_standard_profile(
    shop: Shop, intuit: FakeQuickBooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = login_as(shop.app, shop.business, ["admin"])
    assert admin.post(f"{QB}/connect").status_code == 403
    assert admin.get(QB).status_code == 200
    auditor = login_as(shop.app, shop.business, ["auditor"])
    assert auditor.get(QB).json()["status"] == "not_connected"
    assert auditor.post(f"{QB}/sync").status_code == 403

    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    r = shop.owner.post(f"{QB}/connect")
    assert (r.status_code, r.json()["code"]) == (409, "accounting.regulated_profile")
    monkeypatch.setattr(get_settings(), "data_profile", "standard")
    monkeypatch.setattr(get_settings(), "quickbooks_client_id", "")
    r = shop.owner.post(f"{QB}/connect")
    assert (r.status_code, r.json()["code"]) == (409, "accounting.not_configured")
    assert shop.owner.get(QB).json()["configured"] is False


def test_accounts_listing_and_mapping_validation(books: Shop) -> None:
    accounts = books.owner.get(f"{QB}/accounts").json()
    assert {(a["id"], a["account_type"]) for a in accounts} >= {("1", "Income"), ("2", "Bank"), ("3", "Expense")}
    status = books.owner.get(QB).json()
    assert status["missing_mapping"] == []
    r = books.owner.put(
        f"{QB}/settings", json={"version": status["version"], "sync_from": "2026-10-01", "income_account_id": "x1"}
    )
    assert r.status_code == 422
    r = books.owner.put(f"{QB}/settings", json={"version": status["version"] - 1, "sync_from": "2026-10-01"})
    assert (r.status_code, r.json()["code"]) == (409, "accounting.version_conflict")


def test_full_sync_pushes_sales_and_purchases_once(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    invoice = issued_invoice(books)
    pay(books, invoice, 4000, reference="AUTH-1")
    bill = supplier_bill(books, supplier_reference="INV-77", due_date="2026-11-27")
    r = books.owner.post(
        f"/api/v1/purchasing/bills/{bill['id']}/payments",
        json={"version": bill["version"], "amount_minor": 5000, "paid_on": "2026-10-28", "method": "bank_transfer"},
    )
    assert r.status_code == 201, r.text

    sync(books, registry)

    [customer] = intuit.of("customer")
    assert (customer["DisplayName"], customer["PrimaryEmailAddr"]) == ("Ada Example", {"Address": "ada@example.com"})
    [item] = intuit.of("item")
    assert (item["Name"], item["Type"], item["IncomeAccountRef"]) == ("Tandem sales", "Service", {"value": "1"})
    [qb_invoice] = intuit.of("invoice")
    assert qb_invoice["CustomerRef"] == {"value": customer["Id"]}
    assert qb_invoice["DocNumber"] == invoice["number"] and qb_invoice["TxnDate"] == "2026-10-28"
    [line] = qb_invoice["Line"]
    assert (line["Amount"], line["SalesItemLineDetail"]["Qty"], line["SalesItemLineDetail"]["UnitPrice"]) == (
        90.0,
        2.0,
        45.0,
    )
    assert line["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}
    [payment] = intuit.of("payment")
    assert (payment["TotalAmt"], payment["PaymentRefNum"], payment["DepositToAccountRef"]) == (
        40.0,
        "AUTH-1",
        {"value": "2"},
    )
    assert payment["Line"][0]["LinkedTxn"] == [{"TxnId": qb_invoice["Id"], "TxnType": "Invoice"}]
    [vendor] = intuit.of("vendor")
    assert (vendor["DisplayName"], vendor["PrimaryEmailAddr"]) == ("Example Parts", {"Address": "parts@example.com"})
    [qb_bill] = intuit.of("bill")
    assert (qb_bill["DocNumber"], qb_bill["DueDate"], qb_bill["Line"][0]["Amount"]) == ("INV-77", "2026-11-27", 120.0)
    assert qb_bill["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "3"}
    [bill_payment] = intuit.of("billpayment")
    assert (bill_payment["PayType"], bill_payment["CheckPayment"], bill_payment["TotalAmt"]) == (
        "Check",
        {"BankAccountRef": {"value": "2"}},
        50.0,
    )

    synced = records(books)
    assert {r["entity_type"] for r in synced} == {
        "customer",
        "supplier",
        "invoice",
        "invoice_payment",
        "bill",
        "bill_payment",
    }
    assert {r["status"] for r in synced} == {"synced"}
    status = books.owner.get(QB).json()
    assert status["counts"] == {"synced": 6, "failed": 0, "skipped": 0}
    assert status["last_sync_error"] is None and status["last_sync_finished_at"]

    writes = len(intuit.writes)
    sync(books, registry)  # nothing changed: no writes
    assert len(intuit.writes) == writes

    # paying the rest changes the invoice's version but not its QuickBooks body: only the payment is new
    invoice = books.owner.get(f"{BASE}/invoices/{invoice['id']}").json()
    pay(books, invoice, 5000)
    sync(books, registry)
    assert [w[:2] for w in intuit.writes[writes:]] == [("payment", "create")]


def test_customer_names_are_reused_and_kept_apart(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    intuit.objects["customer"]["7"] = {"Id": "7", "SyncToken": "0", "DisplayName": "Ada Example"}
    intuit.objects["vendor"]["8"] = {"Id": "8", "SyncToken": "0", "DisplayName": "Example Parts"}
    issued_invoice(books)
    supplier_bill(books)
    sync(books, registry)
    assert intuit.of("invoice")[0]["CustomerRef"] == {"value": "7"}  # linked, not duplicated
    assert intuit.of("bill")[0]["VendorRef"] == {"value": "8"}
    assert len(intuit.of("customer")) == 1

    # a second Tandem customer with the same name gets its own QuickBooks customer
    r = books.owner.post("/api/v1/crm/customers", json={"display_name": "Ada Example"})
    other = r.json()["id"]
    books.action("invoices", books.create(customer=other), "issue")
    sync(books, registry)
    names = sorted(c["DisplayName"] for c in intuit.of("customer"))
    assert names[0] == "Ada Example" and names[1].startswith("Ada Example ") and len(names) == 2


def test_duplicate_name_across_customers_and_suppliers(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    intuit.objects["vendor"]["9"] = {"Id": "9", "SyncToken": "0", "DisplayName": "Ada Example"}
    issued_invoice(books)
    sync(books, registry)
    assert [c["DisplayName"] for c in intuit.of("customer")] == ["Ada Example (Customer)"]


def test_payment_void_and_credit_note(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    invoice = issued_invoice(books)
    paid = pay(books, invoice, 9000)
    sync(books, registry)
    payment_id = paid["payments"][0]["id"]
    r = books.owner.post(
        f"{BASE}/invoices/{invoice['id']}/payments/{payment_id}/void", json={"version": paid["version"]}
    )
    assert r.status_code == 200, r.text
    sync(books, registry)
    [qb_payment] = intuit.of("payment")
    assert qb_payment.get("voided") is True

    books.action("invoices", r.json(), "credit")
    sync(books, registry)
    [memo] = intuit.of("creditmemo")
    assert memo["DocNumber"].startswith("CN-") and memo["Line"][0]["Amount"] == 90.0
    application = next(p for p in intuit.of("payment") if p["TotalAmt"] == 0)
    assert [line["LinkedTxn"][0]["TxnType"] for line in application["Line"]] == ["Invoice", "CreditMemo"]
    kinds = {r["entity_type"]: r["status"] for r in records(books)}
    assert kinds["credit_note"] == kinds["credit_application"] == kinds["invoice_payment"] == "synced"
    assert not [w for w in intuit.writes if w[0] == "invoice" and w[1] != "create"]  # invoice itself untouched


def test_voided_before_sync_is_skipped_and_void_bill_is_deleted(
    books: Shop, intuit: FakeQuickBooks, registry: Registry
) -> None:
    bill = supplier_bill(books)
    sync(books, registry)
    r = books.owner.post(f"/api/v1/purchasing/bills/{bill['id']}/void", json={"version": bill["version"]})
    assert r.status_code == 200, r.text
    other = supplier_bill(books, total=500)
    r = books.owner.post(f"/api/v1/purchasing/bills/{other['id']}/void", json={"version": other["version"]})
    assert r.status_code == 200, r.text
    sync(books, registry)
    deleted = [o for o in intuit.of("bill") if o.get("deleted")]
    assert len(deleted) == 1 and len(intuit.of("bill")) == 1
    by_status = sorted((r["status"], r["last_error"]) for r in records(books, entity_type="bill"))
    assert by_status == [("skipped", "Voided in Tandem before it reached QuickBooks"), ("synced", None)]


def test_failures_back_off_and_retry(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    invoice = issued_invoice(books)
    pay(books, invoice, 1000)
    intuit.fail_next["invoice"] = fault(400, "6000", "A business validation error has occurred")
    sync(books, registry)
    failed = {r["entity_type"]: r for r in records(books, status="failed")}
    assert failed["invoice"]["last_error"] == "A business validation error has occurred"
    assert failed["invoice"]["attempts"] == 1 and failed["invoice"]["next_attempt_at"]
    assert failed["invoice_payment"]["last_error"].startswith("Waiting for the invoice")
    assert intuit.of("invoice") == [] and intuit.of("payment") == []

    sync(books, registry)  # inside the back-off: nothing retried
    assert intuit.of("invoice") == []

    r = books.owner.post(f"{QB}/records/{failed['invoice']['id']}/retry")
    assert r.status_code == 200, r.text
    from app.platform import jobs

    jobs.run_pending(registry)
    assert len(intuit.of("invoice")) == 1
    assert {r["entity_type"]: r["status"] for r in records(books)}["invoice"] == "synced"
    r = books.owner.post(f"{QB}/records/{failed['invoice']['id']}/retry")
    assert (r.status_code, r.json()["code"]) == (409, "accounting.record_already_synced")

    # the payment waits for its own back-off, then follows
    later = clock.now() + timedelta(minutes=6)
    clock.set_clock(lambda: later)
    sync(books, registry)
    assert len(intuit.of("payment")) == 1


def test_edit_updates_with_sync_token(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    bill = supplier_bill(books, supplier_reference="INV-1")
    sync(books, registry)
    r = books.owner.patch(
        f"/api/v1/purchasing/bills/{bill['id']}", json={"version": bill["version"], "supplier_reference": "INV-2"}
    )
    assert r.status_code == 200, r.text
    intuit.of("bill")[0]["SyncToken"] = "4"  # edited in QuickBooks meanwhile
    sync(books, registry)
    [qb_bill] = intuit.of("bill")
    assert (qb_bill["DocNumber"], qb_bill["SyncToken"]) == ("INV-2", "5")
    assert ("bill", "update", qb_bill["Id"]) in intuit.writes


def test_other_currency_is_skipped(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, intuit: FakeQuickBooks, registry: Registry
) -> None:
    intuit.home_currency = "CAD"
    connect(shop)
    map_accounts(shop)
    issued_invoice(shop)
    sync(shop, registry)
    [row] = records(shop)
    assert (row["status"], row["last_error"]) == ("skipped", "In USD; the QuickBooks company uses CAD")
    assert intuit.of("invoice") == []


def test_token_refresh_rotation_and_reconnect(
    books: Shop, intuit: FakeQuickBooks, registry: Registry, owner_engine: Engine
) -> None:
    later = clock.now() + timedelta(minutes=58)  # access token about to expire
    clock.set_clock(lambda: later)
    issued_invoice(books)
    sync(books, registry)
    assert intuit.token_calls[-1] == {"grant_type": "refresh_token", "refresh_token": "refresh-1"}
    assert len(intuit.of("invoice")) == 1

    intuit.access = "revoked-elsewhere"  # every call now answers 401
    intuit.refuse_refresh = True
    issued_invoice(books)
    sync(books, registry)
    status = books.owner.get(QB).json()
    assert status["status"] == "needs_reconnect" and "connect again" in status["last_sync_error"]
    with owner_engine.connect() as conn:
        assert conn.execute(text("select refresh_token_sealed from accounting_connections")).scalar() is None
    assert books.owner.post(f"{QB}/sync").json()["code"] == "accounting.not_connected"


def test_throttling_stops_the_run_without_failing_records(
    books: Shop, intuit: FakeQuickBooks, registry: Registry
) -> None:
    issued_invoice(books)
    intuit.fail_next["invoice"] = fault(429, "003001", "ThrottleExceeded")
    sync(books, registry)
    status = books.owner.get(QB).json()
    assert "rate limiting" in status["last_sync_error"]
    assert {r["entity_type"]: r["status"] for r in records(books)}.get("invoice") is None
    sync(books, registry)
    assert len(intuit.of("invoice")) == 1


def test_disconnect_revokes_and_keeps_log(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    issued_invoice(books)
    sync(books, registry)
    status = books.owner.get(QB).json()
    r = books.owner.post(f"{QB}/disconnect", json={"version": status["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "disconnected"
    assert intuit.revoked == ["refresh-1"]
    assert len(records(books)) == 2
    assert books.owner.post(f"{QB}/sync").status_code == 409


def test_export_omits_tokens_and_erasure_redacts_labels(books: Shop, registry: Registry) -> None:
    issued_invoice(books)
    sync(books, registry)
    with transaction(ctx_for(books.business)) as db:
        exported = registry.export_handlers["accounting"](db, ctx_for(books.business))
    [conn] = exported["accounting_connections"]
    assert "access_token_sealed" not in conn and "refresh_token_sealed" not in conn and "oauth_state_hash" not in conn
    assert any("Ada Example" in r["label"] for r in exported["accounting_sync_records"])

    from app.platform.registry import ErasureTarget

    target = ErasureTarget(
        customer_id=uuid4(),
        display_name="Ada Example",
        emails=("ada@example.com",),
        phones=(),
        names=("Ada Example",),
    )
    with transaction(ctx_for(books.business)) as db:
        counts = registry.erasure_handlers["accounting"](db, ctx_for(books.business), target)
    assert counts["accounting_sync_records_label_redacted"] == 2
    assert not any("Ada Example" in r["label"] for r in records(books))


def test_business_isolation(books: Shop, registry: Registry, make_business: Any) -> None:
    other = Shop(books.app, make_business("other-books"))
    assert other.owner.get(QB).json()["status"] == "not_connected"
    assert other.owner.get(f"{QB}/records").json()["items"] == []


# ---- pure pieces ---------------------------------------------------------------------------------------


def _doc(**over: Any) -> LedgerInvoice:
    base: dict[str, Any] = {
        "id": uuid4(),
        "version": 1,
        "kind": "invoice",
        "status": "issued",
        "number": "INV-2026-000000000000042",
        "customer_id": uuid4(),
        "bill_to_name": "Ada: Example",
        "bill_to_email": None,
        "issue_date": date(2026, 10, 28),
        "due_date": None,
        "currency": "USD",
        "subtotal_minor": 1000,
        "tax_minor": 80,
        "total_minor": 1080,
        "credit_of_invoice_id": None,
        "lines": [
            LedgerLine(name="Pads", quantity=Decimal("3"), unit_price_minor=333, subtotal_minor=1000, tax_minor=80)
        ],
    }
    return LedgerInvoice(**{**base, **over})


def test_sales_document_keeps_exact_amounts_and_tax_line() -> None:
    refs = payloads.SalesRefs("5", "11", "12", "40", us_company=False)
    body = payloads.sales_document(_doc(), refs)
    assert len(body["DocNumber"]) == 21 and body["DocNumber"].endswith("042")
    pads, tax = body["Line"]
    assert pads["Description"] == "Pads (x 3)" and pads["Amount"] == Decimal("10.00")
    assert pads["SalesItemLineDetail"]["Qty"] == 1 and "TaxCodeRef" not in pads["SalesItemLineDetail"]
    assert tax["SalesItemLineDetail"]["ItemRef"] == {"value": "12"} and tax["Amount"] == Decimal("0.80")
    assert body["ARAccountRef"] == {"value": "40"}
    with pytest.raises(ValueError):
        payloads.sales_document(_doc(), payloads.SalesRefs("5", "11", None, None, us_company=True))
    assert payloads.clean_name("Ada:  Example\n") == "Ada- Example"
    yen = payloads.amount(1500, "JPY")
    assert yen == Decimal(1500)


def test_query_literals_and_faults() -> None:
    assert quote_literal("O'Brien \\ Co") == "'O\\'Brien \\\\ Co'"
    assert select_where("Customer", "DisplayName", "x") == "select * from Customer where DisplayName = 'x'"
    with pytest.raises(ValueError):
        select_where("Customer; drop", "DisplayName", "x")
    import httpx

    kinds = {
        (401, "3200"): "auth",
        (429, "003001"): "throttled",
        (400, "6240"): "duplicate_name",
        (400, "5010"): "stale",
        (400, "6000"): "validation",
        (503, "0"): "unavailable",
    }
    for (status, code), kind in kinds.items():
        response = fault(status, code, "m")
        response.request = httpx.Request("GET", "https://sandbox-quickbooks.api.intuit.com/x")
        error = _fault(response)
        assert isinstance(error, LedgerError) and error.kind == kind


def test_older_bill_sent_for_a_payment_stays_watched(books: Shop, intuit: FakeQuickBooks, registry: Registry) -> None:
    bill = supplier_bill(books, bill_date="2026-10-01")  # before sync_from
    r = books.owner.post(
        f"/api/v1/purchasing/bills/{bill['id']}/payments",
        json={"version": bill["version"], "amount_minor": 5000, "paid_on": "2026-10-28", "method": "bank_transfer"},
    )
    assert r.status_code == 201, r.text
    sync(books, registry)
    assert len(intuit.of("bill")) == 1 and len(intuit.of("billpayment")) == 1

    paid = r.json()
    r = books.owner.post(
        f"/api/v1/purchasing/bills/{bill['id']}/payments/{paid['payments'][0]['id']}/void",
        json={"version": paid["version"]},
    )
    assert r.status_code == 200, r.text
    r = books.owner.post(f"/api/v1/purchasing/bills/{bill['id']}/void", json={"version": r.json()["version"]})
    assert r.status_code == 200, r.text
    sync(books, registry)
    assert intuit.of("bill")[0].get("deleted") is True
    assert intuit.of("billpayment")[0].get("voided") is True
