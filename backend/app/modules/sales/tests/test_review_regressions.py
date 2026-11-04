"""Independent review regressions exercised through proposals, API requests and stored documents."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import jobs
from app.platform.db import transaction
from app.platform.errors import Conflict
from app.platform.registry import ExecutionMeta, Registry
from app.platform.testing.auth import ctx_for

from .factories import BASE, Shop


def propose(shop: Shop, *, explicit_tax: bool = False) -> dict[str, Any]:
    line: dict[str, Any] = {"catalog_item_id": shop.brake, "quantity": "1"}
    if explicit_tax:
        line["tax_rate"] = "0.0825"
    result = shop.owner.post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "sales.create_quote",
            "title": "Quote for Ada Example",
            "payload": {"customer_id": shop.customer, "lines": [line]},
        },
    )
    assert result.status_code == 201, result.text
    return dict(result.json())


def approve(shop: Shop, proposal: dict[str, Any]) -> None:
    admin = login_as(shop.app, shop.business, ["admin"])
    revision = proposal["current_revision"]
    decision = admin.post(
        f"/api/v1/approvals/proposals/{proposal['id']}/decisions",
        json={
            "decision": "approve",
            "revision_id": revision["id"],
            "payload_hash": revision["payload_hash"],
        },
    )
    assert decision.status_code == 200, decision.text


def test_quote_proposal_without_tax_executes_after_payload_round_trip(shop: Shop, registry: Registry) -> None:
    proposal = propose(shop)
    revision = proposal["current_revision"]
    stored = revision["payload"]
    assert "tax_rate" not in stored["lines"][0]
    assert stored["pricing"]["lines"][0]["unit_price_minor"] == 4500
    assert stored["pricing"]["subtotal_minor"] == stored["pricing"]["total_minor"] == 4500
    assert [(line["label"], line["after"]) for line in revision["preview"]] == [  # what the approver reads
        ("Customer", "Ada Example"),
        ("Brake service", "1 × 45.00 USD = 45.00 USD"),
        ("Subtotal", "45.00 USD"),
        ("Tax", "0.00 USD"),
        ("Total", "45.00 USD"),
    ]
    approve(shop, proposal)
    jobs.run_pending(registry)
    done = shop.owner.get(f"/api/v1/approvals/proposals/{proposal['id']}").json()
    assert done["status"] == "succeeded", done
    quote_id = done["attempts"][0]["result"]["quote_id"]
    quote = shop.owner.get(f"{BASE}/quotes/{quote_id}").json()
    assert quote["lines"][0]["unit_price_minor"] == 4500 and quote["total_minor"] == 4500


@pytest.mark.parametrize("change", ["catalog_price", "default_tax"])
def test_changed_proposal_pricing_rejects_execution_without_creating_quote(
    shop: Shop,
    registry: Registry,
    change: str,
) -> None:
    proposal = propose(shop, explicit_tax=(change == "catalog_price"))
    revision = proposal["current_revision"]
    if change == "catalog_price":
        item = shop.owner.get(f"/api/v1/catalog/items/{shop.brake}").json()
        changed = shop.owner.patch(
            f"/api/v1/catalog/items/{shop.brake}",
            json={
                "version": item["version"],
                "price_minor": 9900,
            },
        )
    else:
        current = shop.owner.get("/api/v1/org/settings").json()
        changed = shop.owner.patch(
            "/api/v1/org/settings",
            json={
                "version": current["version"],
                "settings": {**current["settings"], "sales_tax_rate": "0.1"},
            },
        )
    assert changed.status_code == 200, changed.text
    spec = registry.executors["sales.create_quote"]
    payload = spec.payload_model.model_validate(revision["payload"])
    admin = login_as(shop.app, shop.business, ["admin"])
    ctx = ctx_for(shop.business, admin.permissions, user_id=admin.user_id)
    meta = ExecutionMeta(uuid.UUID(proposal["id"]), uuid.UUID(revision["id"]), "price-regression")
    with pytest.raises(Conflict) as error, transaction(ctx) as db:
        spec.run(db, ctx, payload, meta)
    assert error.value.status == 409 and error.value.code == "sales.price_changed"
    approve(shop, proposal)
    jobs.run_pending(registry)
    done = shop.owner.get(f"/api/v1/approvals/proposals/{proposal['id']}").json()
    assert done["status"] == "failed" and done["failure_code"] == "sales.price_changed"
    assert done["current_revision"]["payload"] == revision["payload"]
    assert shop.owner.get(f"{BASE}/quotes").json()["items"] == []


@pytest.mark.parametrize(("currency", "amount"), [("JPY", "100"), ("USD", "1.00"), ("KWD", "0.100")])
def test_csv_exports_document_currency_units(registry: Registry, currency: str, amount: str) -> None:
    shop = Shop(create_app(registry), make_business(f"currency-{currency.lower()}", currency=currency))
    draft = shop.create(lines=[{"name": "Labour", "quantity": "1", "unit_price_minor": 100, "tax_rate": "0"}])
    invoice = shop.action("invoices", draft, "issue")
    response = shop.owner.get(
        f"{BASE}/invoices/export.csv",
        params={
            "from": invoice["issue_date"],
            "to": invoice["issue_date"],
        },
    )
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1 and rows[0]["currency"] == currency
    assert rows[0]["subtotal"] == rows[0]["total"] == amount


@pytest.mark.parametrize("family", ["quotes", "orders", "invoices"])
def test_create_idempotency_replays_response_and_rejects_changed_body(
    shop: Shop,
    owner_engine: Engine,
    family: str,
) -> None:
    body = {"customer_id": shop.customer, "lines": [{"catalog_item_id": shop.brake, "quantity": "1"}]}
    headers = {"Idempotency-Key": f"sales-create-{family}"}
    first = shop.owner.post(f"{BASE}/{family}", json=body, headers=headers)
    repeated = shop.owner.post(f"{BASE}/{family}", json=body, headers=headers)
    assert first.status_code == repeated.status_code == 201, repeated.text
    assert repeated.json() == first.json()
    assert len(shop.owner.get(f"{BASE}/{family}").json()["items"]) == 1
    changed = shop.owner.post(f"{BASE}/{family}", json={**body, "notes": "Different"}, headers=headers)
    assert changed.status_code == 409 and changed.json()["code"] == "platform.idempotency_key_reused"
    with owner_engine.connect() as conn:
        count = conn.scalar(
            text("select count(*) from audit_events where target_id=:id and action like 'sales.%'"),
            {"id": first.json()["id"]},
        )
    assert count == 1


def test_create_idempotency_key_cannot_cross_document_families(shop: Shop) -> None:
    body = {"customer_id": shop.customer, "lines": [{"catalog_item_id": shop.brake, "quantity": "1"}]}
    headers = {"Idempotency-Key": "sales-cross-family"}
    assert shop.owner.post(f"{BASE}/quotes", json=body, headers=headers).status_code == 201
    conflict = shop.owner.post(f"{BASE}/invoices", json=body, headers=headers)
    assert conflict.status_code == 409 and conflict.json()["code"] == "platform.idempotency_key_reused"
    assert shop.owner.get(f"{BASE}/invoices").json()["items"] == []


def test_creating_conversions_and_credit_replay_before_version_checks(shop: Shop) -> None:
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    quote = shop.action("quotes", shop.create("quotes"), "send")
    quote = shop.action("quotes", quote, "accept")

    def create_twice(path: str, version: int, key: str) -> dict[str, Any]:
        headers = {"Idempotency-Key": key}
        first = shop.owner.post(BASE + path, json={"version": version}, headers=headers)
        again = shop.owner.post(BASE + path, json={"version": version}, headers=headers)
        assert first.status_code == again.status_code == 201, again.text
        assert first.json() == again.json()
        return dict(first.json())

    order = create_twice(f"/quotes/{quote['id']}/convert", quote["version"], "sales-quote-convert")
    order = shop.action("orders", order, "confirm")
    invoice = create_twice(f"/orders/{order['id']}/invoice", order["version"], "sales-order-invoice")
    invoice = shop.action("invoices", invoice, "issue")
    credit = create_twice(f"/invoices/{invoice['id']}/credit", invoice["version"], "sales-invoice-credit")
    assert credit["kind"] == "credit_note"


def test_mark_paid_credit_note_returns_conflict_and_leaves_documents_unchanged(
    shop: Shop, owner_engine: Engine
) -> None:
    original = shop.action("invoices", shop.create(), "issue")
    credit = shop.action("invoices", original, "credit")
    failed = shop.owner.post(f"{BASE}/invoices/{credit['id']}/mark-paid", json={"version": credit["version"]})
    assert failed.status_code == 409 and failed.json()["code"] == "sales.invalid_transition"
    assert shop.owner.get(f"{BASE}/invoices/{credit['id']}").json() == credit
    assert shop.owner.get(f"{BASE}/invoices/{original['id']}").json()["status"] == "void"
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from audit_events where action='sales.invoice.mark-paid'")) == 0


def test_document_dates_and_numbering_follow_business_midnight(shop: Shop, owner_engine: Engine) -> None:
    from datetime import UTC, datetime

    from app.platform import clock

    with owner_engine.begin() as conn:
        conn.execute(text("update businesses set timezone='America/New_York' where id=:b"), {"b": shop.business})
    clock.set_clock(lambda: datetime(2027, 1, 1, 1, tzinfo=UTC))
    shop.owner = login_as(shop.app, shop.business, ["owner"])
    quote = shop.action("quotes", shop.create("quotes"), "send")
    assert quote["issue_date"] == "2026-12-31" and quote["number"].startswith("QUO-2026-")
    invoice = shop.action("invoices", shop.create(), "issue")
    credit = shop.action("invoices", invoice, "credit")
    assert invoice["issue_date"] == credit["issue_date"] == "2026-12-31"
    assert invoice["number"].startswith("INV-2026-") and credit["number"].startswith("CN-2026-")
