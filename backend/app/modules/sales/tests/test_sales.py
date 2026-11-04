"""Sales business rules through the staff API and shared transaction services."""

from __future__ import annotations

import csv
import io
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.platform import clock
from app.platform.db import transaction
from app.platform.registry import ErasureTarget, Registry
from app.platform.testing.auth import ctx_for

from .. import lifecycle, permissions, service
from .factories import BASE, Shop


def test_totals_half_up_and_draft_edit_version(shop: Shop) -> None:
    # Price is integer minor units: 3.335 units at 3 cents is the requested half-minor-unit case.
    doc = shop.create(
        lines=[{"name": "Fractional service", "quantity": "3.335", "unit_price_minor": 3, "tax_rate": "0.0825"}]
    )
    assert (doc["subtotal_minor"], doc["tax_minor"], doc["total_minor"]) == (10, 1, 11)
    r = shop.owner.patch(
        f"{BASE}/invoices/{doc['id']}",
        json={
            "version": doc["version"],
            "lines": [{"name": "Half", "quantity": "1.5", "unit_price_minor": 3, "tax_rate": "0.1"}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["subtotal_minor"] == 5 and r.json()["tax_minor"] == 1
    stale = shop.owner.patch(f"{BASE}/invoices/{doc['id']}", json={"version": doc["version"], "notes": "stale"})
    assert stale.status_code == 409


def test_quote_order_invoice_and_stock_lifecycle(shop: Shop) -> None:
    quote = shop.action("quotes", shop.create("quotes"), "send")
    assert quote["number"] == "QUO-2026-0001"
    quote = shop.action("quotes", quote, "accept")
    order = shop.action("quotes", quote, "convert")
    assert order["source_quote_id"] == quote["id"]
    order = shop.action("orders", order, "confirm")
    assert order["number"] == "SO-2026-0001"
    stock = shop.available()
    assert Decimal(stock["reserved"]) == 1
    order = shop.action("orders", order, "fulfil")
    stock = shop.available()
    assert Decimal(stock["reserved"]) == 0 and Decimal(stock["on_hand"]) == 9
    ledger = shop.owner.get(f"/api/v1/inventory/items/{shop.part}").json()["movements"]
    sale = next(m for m in ledger if m["ref_type"] == "sales_order" and m["ref_id"] == order["id"])
    assert sale["reason"] == "sale" and Decimal(sale["quantity"]) == -1
    invoice = shop.action("orders", order, "invoice")
    assert invoice["lines"][0]["name"] == quote["lines"][0]["name"]
    invoice = shop.action("invoices", invoice, "issue")
    assert invoice["number"] == "INV-2026-0001" and invoice["total_minor"] == quote["total_minor"]
    paid = shop.action("invoices", invoice, "mark-paid")
    assert paid["paid_at"] is not None and paid["status"] == "paid"


def test_cancel_releases_reservations(shop: Shop) -> None:
    order = shop.action("orders", shop.create("orders"), "confirm")
    shop.action("orders", order, "cancel")
    assert Decimal(shop.available()["reserved"]) == 0
    assert Decimal(shop.available()["on_hand"]) == 10


def test_issued_immutable_credit_voids_original(shop: Shop) -> None:
    invoice = shop.action("invoices", shop.create(), "issue")
    r = shop.owner.patch(f"{BASE}/invoices/{invoice['id']}", json={"version": invoice["version"], "notes": "change"})
    assert r.status_code == 409 and r.json()["code"] == "sales.document_locked"
    credit = shop.action("invoices", invoice, "credit")
    assert credit["number"] == "CN-2026-0001" and credit["kind"] == "credit_note"
    assert credit["total_minor"] == invoice["total_minor"] and credit["credit_of_invoice_id"] == invoice["id"]
    assert shop.owner.get(f"{BASE}/invoices/{invoice['id']}").json()["status"] == "void"
    r = shop.owner.post(f"{BASE}/invoices/{invoice['id']}/credit", json={"version": invoice["version"] + 1})
    assert r.status_code == 409


@pytest.mark.concurrency
def test_concurrent_numbering_and_rolled_back_issue(shop: Shop) -> None:
    docs = [shop.create() for _ in range(4)]
    ctx = ctx_for(shop.business, permissions.ALL)
    barrier = threading.Barrier(4)

    def issue(doc: dict[str, Any]) -> str:
        barrier.wait(timeout=10)
        with transaction(ctx) as db:
            row = service.transition(db, ctx, "invoice", uuid.UUID(doc["id"]), doc["version"], "issue")
            return str(row.number)

    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(issue, docs))
    assert sorted(numbers) == [f"INV-2026-{i:04d}" for i in range(1, 5)]
    draft = shop.create()
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction(ctx) as db:
            service.transition(db, ctx, "invoice", uuid.UUID(draft["id"]), draft["version"], "issue")
            raise RuntimeError("rollback")
    assert shop.action("invoices", draft, "issue")["number"] == "INV-2026-0005"


def test_permissions_tenant_isolation_and_csv(shop: Shop, other_business: uuid.UUID) -> None:
    doc = shop.action("invoices", shop.create(), "issue")
    auditor = login_as(shop.app, shop.business, ["auditor"])
    assert auditor.get(f"{BASE}/invoices").status_code == 200
    assert auditor.post(f"{BASE}/invoices", json={"customer_id": shop.customer, "lines": []}).status_code == 403
    operator = login_as(shop.app, shop.business, ["operator"])
    assert operator.post(f"{BASE}/invoices/{doc['id']}/credit", json={"version": doc["version"]}).status_code == 403
    assert operator.post(f"{BASE}/invoices/{doc['id']}/mark-paid", json={"version": doc["version"]}).status_code == 200
    other = login_as(shop.app, other_business, ["owner"])
    assert other.get(f"{BASE}/invoices").json()["items"] == []
    assert other.get(f"{BASE}/invoices/{doc['id']}").status_code == 404
    r = auditor.get(f"{BASE}/invoices/export.csv", params={"from": "2026-10-01", "to": "2026-10-31"})
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert r.status_code == 200 and len(rows) == 1
    assert rows[0]["number"] == doc["number"] and rows[0]["total"] == "63.00"


def test_erasure_preserves_legal_snapshot(shop: Shop, registry: Registry) -> None:
    quote = shop.create("quotes")
    draft = shop.create()
    issued = shop.action("invoices", shop.create(), "issue")
    target = ErasureTarget(uuid.UUID(shop.customer), "Ada Example", ("ada@example.com",), ())
    ctx = ctx_for(shop.business)
    with transaction(ctx) as db:
        assert {uuid.UUID(x["id"]) for x in (quote, draft, issued)} <= lifecycle.links(db, ctx, target)
        registry.erasure_handlers["sales"](db, ctx, target)
    for family, doc in (("quotes", quote), ("invoices", draft)):
        erased = shop.owner.get(f"{BASE}/{family}/{doc['id']}").json()
        assert erased["bill_to_name"] == erased["bill_to_email"] == erased["bill_to_address"] == "[erased]"
        assert erased["notes"] is None
    retained = shop.owner.get(f"{BASE}/invoices/{issued['id']}").json()
    assert retained["bill_to_name"] == "Ada Example" and retained["bill_to_email"] == "ada@example.com"
    assert retained["bill_to_address"] == "1 Example Lane"


def test_invoice_from_completed_appointment(shop: Shop) -> None:
    resource = shop.owner.post("/api/v1/scheduling/resources", json={"name": "Jordan Example"})
    assert resource.status_code == 201, resource.text
    booking = shop.owner.post(
        "/api/v1/scheduling/appointments",
        json={
            "customer_id": shop.customer,
            "service_id": shop.brake,
            "resource_id": resource.json()["id"],
            "starts_at": "2026-10-29T16:00:00Z",
            "override_hours": True,
        },
    )
    assert booking.status_code == 201, booking.text
    appointment = booking.json()
    premature = shop.owner.post(f"{BASE}/invoices/from-appointment/{appointment['id']}")
    assert premature.status_code == 409 and premature.json()["code"] == "sales.invalid_transition"
    clock.set_clock(lambda: datetime(2026, 10, 29, 18, tzinfo=UTC))
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    completed = shop.owner.post(
        f"/api/v1/scheduling/appointments/{appointment['id']}/complete", json={"version": appointment["version"]}
    )
    assert completed.status_code == 200, completed.text
    headers = {"Idempotency-Key": "sales-appointment-invoice"}
    invoice = shop.owner.post(f"{BASE}/invoices/from-appointment/{appointment['id']}", headers=headers)
    assert invoice.status_code == 201, invoice.text
    repeated = shop.owner.post(f"{BASE}/invoices/from-appointment/{appointment['id']}", headers=headers)
    assert repeated.status_code == 201 and repeated.json() == invoice.json()
    doc = invoice.json()
    assert doc["status"] == "draft" and doc["number"] is None
    assert doc["source_appointment_id"] == appointment["id"] and doc["customer_id"] == shop.customer
    assert [line["catalog_item_id"] for line in doc["lines"]] == [shop.brake]
    assert shop.owner.post(f"{BASE}/invoices/from-appointment/{appointment['id']}").status_code == 409


def test_failed_first_issue_rolls_back_sequence_and_audit(shop: Shop, owner_engine: Engine) -> None:
    draft = shop.create()
    ctx = ctx_for(shop.business, permissions.ALL)
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction(ctx) as db:
            service.transition(db, ctx, "invoice", uuid.UUID(draft["id"]), draft["version"], "issue")
            raise RuntimeError("rollback")
    with owner_engine.connect() as conn:
        assert (
            conn.scalar(text("select count(*) from document_sequences where business_id=:b"), {"b": shop.business}) == 0
        )
        assert conn.scalar(text("select count(*) from audit_events where action='sales.invoice.issue'")) == 0
    assert shop.action("invoices", draft, "issue")["number"] == "INV-2026-0001"


def test_snapshot_survives_catalog_and_customer_edits(shop: Shop) -> None:
    quote = shop.create("quotes")
    customer = shop.owner.get(f"/api/v1/crm/customers/{shop.customer}").json()
    changed = shop.owner.patch(
        f"/api/v1/crm/customers/{shop.customer}",
        json={
            "version": customer["version"],
            "display_name": "Ada Changed",
        },
    )
    assert changed.status_code == 200, changed.text
    item = shop.owner.get(f"/api/v1/catalog/items/{shop.brake}").json()
    changed_item = shop.owner.patch(
        f"/api/v1/catalog/items/{shop.brake}",
        json={
            "version": item["version"],
            "name": "New service name",
            "price_minor": 9000,
        },
    )
    assert changed_item.status_code == 200, changed_item.text
    quote = shop.action("quotes", quote, "send")
    quote = shop.action("quotes", quote, "accept")
    order = shop.action("quotes", quote, "convert")
    assert order["bill_to_name"] == "Ada Example"
    assert order["lines"][0]["name"] == "Brake service" and order["lines"][0]["unit_price_minor"] == 4500


def test_transition_guards_and_duplicate_conversion(shop: Shop) -> None:
    draft = shop.create("quotes")
    premature = shop.owner.post(f"{BASE}/quotes/{draft['id']}/accept", json={"version": draft["version"]})
    assert premature.status_code == 409 and premature.json()["code"] == "sales.invalid_transition"
    sent = shop.action("quotes", draft, "send")
    locked = shop.owner.patch(f"{BASE}/quotes/{sent['id']}", json={"version": sent["version"], "notes": "edit"})
    assert locked.status_code == 409 and locked.json()["code"] == "sales.document_locked"
    accepted = shop.action("quotes", sent, "accept")
    shop.action("quotes", accepted, "convert")
    current = shop.owner.get(f"{BASE}/quotes/{accepted['id']}").json()
    duplicate = shop.owner.post(f"{BASE}/quotes/{current['id']}/convert", json={"version": current["version"]})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "sales.already_converted"


def test_rls_denies_cross_business_and_export_covers_all_tables(
    shop: Shop,
    other_business: uuid.UUID,
    registry: Registry,
) -> None:
    draft = shop.create()
    with transaction(ctx_for(other_business)) as db:
        assert db.scalar(text("select count(*) from invoices where id=:id"), {"id": draft["id"]}) == 0
    with transaction(ctx_for(shop.business)) as db:
        exported = registry.export_handlers["sales"](db, ctx_for(shop.business))
    assert set(exported) == {
        "document_sequences",
        "quotes",
        "quote_lines",
        "sales_orders",
        "sales_order_lines",
        "invoices",
        "invoice_lines",
    }
    assert len(exported["invoices"]) == 1 and len(exported["invoice_lines"]) == 2


def test_executor_registration_validation_and_run(shop: Shop, registry: Registry) -> None:
    from app.platform.registry import ExecutionMeta

    create_spec = registry.executors["sales.create_quote"]
    confirm_spec = registry.executors["sales.confirm_order"]
    payload = create_spec.payload_model.model_validate(
        {
            "customer_id": shop.customer,
            "lines": [{"catalog_item_id": shop.brake, "quantity": "1"}],
        }
    )
    approver = login_as(shop.app, shop.business, ["admin"])
    ctx = ctx_for(shop.business, approver.permissions, user_id=approver.user_id)
    meta = ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "sales-test")
    with transaction(ctx) as db:
        create_spec.validate(db, ctx, payload)
        assert create_spec.describe(db, ctx, payload)
        result = create_spec.run(db, ctx, payload, meta)
    quote = shop.owner.get(f"{BASE}/quotes/{result['quote_id']}").json()
    assert quote["status"] == "draft"
    quote = shop.action("quotes", quote, "send")
    quote = shop.action("quotes", quote, "accept")
    order = shop.action("quotes", quote, "convert")
    confirm = confirm_spec.payload_model.model_validate({"order_id": order["id"], "version": order["version"]})
    with transaction(ctx) as db:
        confirm_spec.validate(db, ctx, confirm)
        assert confirm_spec.describe(db, ctx, confirm)
        assert confirm_spec.run(db, ctx, confirm, meta)["order_id"] == order["id"]


def test_operator_draft_issue_permissions(shop: Shop) -> None:
    operator = login_as(shop.app, shop.business, ["operator"])
    created = operator.post(
        f"{BASE}/invoices",
        json={
            "customer_id": shop.customer,
            "lines": [{"catalog_item_id": shop.brake, "quantity": "1"}],
        },
    )
    assert created.status_code == 201, created.text
    doc = created.json()
    edited = operator.patch(f"{BASE}/invoices/{doc['id']}", json={"version": doc["version"], "notes": "Check brakes"})
    assert edited.status_code == 200, edited.text
    assert (
        operator.post(f"{BASE}/invoices/{doc['id']}/issue", json={"version": edited.json()["version"]}).status_code
        == 200
    )
