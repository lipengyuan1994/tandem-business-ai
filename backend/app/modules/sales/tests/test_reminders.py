"""Manual overdue follow-up: business-local reads, approval execution and transactional outbox."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock, events, jobs
from app.platform.config import get_settings
from app.platform.db import transaction
from app.platform.errors import Conflict, Forbidden
from app.platform.registry import ExecutionMeta, Registry
from app.platform.testing.auth import ctx_for

from .factories import BASE, Shop

SMTP_URL = "smtp://mail.example.com:587?from=no-reply@example.com"


def invoice(shop: Shop, due: str | None, status: str = "issued") -> dict[str, Any]:
    draft = shop.create()
    r = shop.owner.patch(f"{BASE}/invoices/{draft['id']}", json={"version": draft["version"], "due_date": due})
    assert r.status_code == 200, r.text
    row = dict(r.json())
    if status != "draft":
        row = shop.action("invoices", row, "issue")
    if status == "paid":
        row = shop.action("invoices", row, "mark-paid")
    if status == "void":
        shop.action("invoices", row, "credit")
    return row


@pytest.mark.parametrize("hour, local_day", [(5, 31), (7, 1), (8, 1)])
def test_list_overdue_invoices_only_issued_unpaid_past_due_in_business_timezone(
    shop: Shop, hour: int, local_day: int
) -> None:
    from app.modules.sales.public import list_overdue_invoices

    oldest = invoice(shop, "2026-10-01")
    recent = invoice(shop, "2026-10-31")
    for due, status in (
        ("2026-10-01", "paid"),
        ("2026-10-01", "void"),
        ("2026-10-01", "draft"),
        ("2026-11-01", "issued"),
        ("2026-11-02", "issued"),
        (None, "issued"),
    ):
        invoice(shop, due, status)
    other_business = make_business("overdue-other", timezone="Europe/London", currency="GBP")
    other_row = invoice(Shop(shop.app, other_business), "2026-10-01")
    # DST fall-back: both occurrences of 01:00 in Denver have the same local date.
    clock.set_clock(lambda: datetime(2026, 11, 1, hour, tzinfo=UTC))
    ctx = ctx_for(shop.business, ["sales.document.read"])
    with transaction(ctx) as db:
        rows = list_overdue_invoices(db, ctx)
        assert [str(r.id) for r in rows] == [oldest["id"]] + ([recent["id"]] if local_day == 1 else [])
        assert rows[0].days_overdue == (31 if local_day == 1 else 30)
        assert rows[0].bill_to_email == "ada@example.com" and rows[0].last_reminder_at is None
        assert len(list_overdue_invoices(db, ctx, as_of=date(2026, 11, 2), limit=1)) == 1
        assert list_overdue_invoices(db, ctx, as_of=date(2026, 10, 1)) == []
    with transaction(ctx_for(other_business, ["sales.document.read"])) as db:
        assert [str(r.id) for r in list_overdue_invoices(db, ctx_for(other_business, ["sales.document.read"]))] == [
            other_row["id"]
        ]
    with pytest.raises(Forbidden), transaction(ctx_for(shop.business)) as db:
        list_overdue_invoices(db, ctx_for(shop.business))


@pytest.mark.parametrize("reason", ["email", "paid", "draft", "today", "future", "no_due", "void", "credit"])
def test_payment_reminder_refused_without_email_or_when_paid(
    shop: Shop, owner_engine: Engine, registry: Registry, reason: str
) -> None:
    row = invoice(
        shop,
        {"today": "2026-10-28", "future": "2026-10-29", "no_due": None}.get(reason, "2026-10-01"),
        reason if reason in {"paid", "draft", "void"} else "issued",
    )
    if reason == "credit":
        row = shop.action("invoices", row, "credit")
    if reason == "email":
        with owner_engine.begin() as conn:
            conn.execute(text("update invoices set bill_to_email=null where id=:id"), {"id": row["id"]})
    row = shop.owner.get(f"{BASE}/invoices/{row['id']}").json()
    response = shop.owner.post(
        f"{BASE}/invoices/{row['id']}/reminders", json={"version": row["version"], "body": "Please pay"}
    )
    assert response.status_code == 409, response.text
    spec = registry.executors["sales.send_payment_reminder"]
    payload = spec.payload_model.model_validate(
        {"invoice_id": row["id"], "invoice_version": row["version"], "body": "Please pay"}
    )
    ctx = ctx_for(shop.business, ["sales.document.write"])
    with pytest.raises(Conflict), transaction(ctx) as db:
        spec.run(db, ctx, payload, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "refused"))


def test_reminder_revalidates_at_execution_and_rolls_back(shop: Shop, registry: Registry, owner_engine: Engine) -> None:
    row = invoice(shop, "2026-10-01")
    spec = registry.executors["sales.send_payment_reminder"]
    payload = spec.payload_model.model_validate(
        {"invoice_id": row["id"], "invoice_version": row["version"], "body": "Please pay"}
    )
    ctx = ctx_for(shop.business, ["sales.document.write"])
    meta = ExecutionMeta(uuid.uuid4(), uuid.uuid4(), "rollback")
    with pytest.raises(RuntimeError), transaction(ctx) as db:
        spec.run(db, ctx, payload, meta)
        raise RuntimeError("rollback")
    assert shop.owner.get(f"{BASE}/invoices/{row['id']}").json()["last_reminder_at"] is None
    with owner_engine.connect() as conn:
        assert conn.scalar(text("select count(*) from outbox_messages where related_id=:id"), {"id": row["id"]}) == 0
        assert conn.scalar(text("select count(*) from audit_events where action='sales.payment_reminder.sent'")) == 0
    shop.action("invoices", row, "mark-paid")
    with pytest.raises(Conflict), transaction(ctx) as db:
        spec.run(db, ctx, payload, meta)


def test_overdue_filter_and_reminder_permissions_and_body_limits(shop: Shop, other_business: uuid.UUID) -> None:
    row = invoice(shop, "2026-10-01")
    invoice(shop, "2026-10-28")
    invoice(shop, "2026-10-01", "paid")
    invoice(shop, "2026-10-01", "void")
    filtered = shop.owner.get(f"{BASE}/invoices", params={"overdue": True}).json()
    assert [r["id"] for r in filtered["items"]] == [row["id"]]
    auditor = login_as(shop.app, shop.business, ["auditor"])
    path = f"{BASE}/invoices/{row['id']}/reminders"
    assert auditor.post(path, json={"version": row["version"], "body": "Please pay"}).status_code == 403
    other = login_as(shop.app, other_business, ["owner"])
    assert other.post(path, json={"version": row["version"], "body": "Please pay"}).status_code == 404
    for body in ("", "x" * 2001):
        assert shop.owner.post(path, json={"version": row["version"], "body": body}).status_code == 422


def queue_reminder(shop: Shop, registry: Registry, key: str) -> None:
    """Run the approved action directly, as the approvals engine does."""
    row = invoice(shop, "2026-10-01")
    spec = registry.executors["sales.send_payment_reminder"]
    payload = spec.payload_model.model_validate(
        {"invoice_id": row["id"], "invoice_version": row["version"], "body": "Please pay"}
    )
    ctx = ctx_for(shop.business, ["sales.document.write"])
    with transaction(ctx) as db:
        spec.run(db, ctx, payload, ExecutionMeta(uuid.uuid4(), uuid.uuid4(), key))


@pytest.mark.parametrize(("smtp_url", "channel"), [("", "simulated"), (SMTP_URL, "email")])
def test_payment_reminder_uses_the_email_channel_only_when_smtp_is_configured(
    shop: Shop, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, smtp_url: str, channel: str
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", smtp_url)
    queue_reminder(shop, registry, "channel")
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select channel, status, recipient, template_key from outbox_messages")).all()
    assert [tuple(r) for r in rows] == [(channel, "pending", "ada@example.com", "payment_reminder")]


def test_the_worker_delivers_a_payment_reminder_without_smtp(
    shop: Shop, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", "")
    queue_reminder(shop, registry, "delivered")
    for _ in range(3):  # what the worker does
        jobs.run_pending(registry)
        events.deliver_pending(registry)
    with owner_engine.connect() as conn:
        row = conn.execute(text("select channel, status, provider_message_id, last_error from outbox_messages")).one()
    assert (row.channel, row.status, row.last_error) == ("simulated", "delivered", None)
    assert row.provider_message_id.startswith("simulated-")
