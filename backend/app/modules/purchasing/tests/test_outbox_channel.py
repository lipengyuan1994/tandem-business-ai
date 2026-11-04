"""Supplier e-mails follow the shared outbox rule: `simulated` until SMTP is configured (M10 rule 3a)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.purchasing.schemas_quotes import SupplierFollowup
from app.platform import events, jobs
from app.platform.config import get_settings
from app.platform.registry import Registry

from .test_purchasing import action, create
from .test_quotes import execute, request_quotes, second_supplier

SMTP_URL = "smtp://mail.example.com:587?from=no-reply@example.com"


def queue_supplier_emails(shop: Any, registry: Registry) -> None:
    """Two quote requests, a follow-up about the request and a follow-up about a purchase order."""
    supplier = shop["supplier"]["id"]
    request_id = request_quotes(shop, registry, [supplier, second_supplier(shop)])
    execute(
        shop,
        registry,
        "send_supplier_followup",
        SupplierFollowup(supplier_id=supplier, about={"quote_request_id": request_id}, body="Example followup"),
    )
    po = action(shop, action(shop, create(shop), "approve"), "send")
    execute(
        shop,
        registry,
        "send_supplier_followup",
        SupplierFollowup(supplier_id=supplier, about={"purchase_order_id": po["id"]}, body="Example chase"),
    )


def outbox(owner_engine: Engine) -> list[tuple[str, str, str]]:
    with owner_engine.connect() as db:
        rows = db.execute(text("select template_key, channel, status from outbox_messages order by template_key"))
        return [(r.template_key, r.channel, r.status) for r in rows]


@pytest.mark.parametrize(("smtp_url", "channel"), [("", "simulated"), (SMTP_URL, "email")])
def test_supplier_emails_use_the_email_channel_only_when_smtp_is_configured(
    shop: Any, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, smtp_url: str, channel: str
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", smtp_url)
    queue_supplier_emails(shop, registry)
    assert outbox(owner_engine) == [
        ("quote_request", channel, "pending"),
        ("quote_request", channel, "pending"),
        ("supplier_followup", channel, "pending"),
        ("supplier_followup", channel, "pending"),
    ]


def test_the_worker_delivers_supplier_emails_without_smtp(
    shop: Any, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", "")
    queue_supplier_emails(shop, registry)
    for _ in range(3):  # what the worker does
        jobs.run_pending(registry)
        events.deliver_pending(registry)
    with owner_engine.connect() as db:
        rows = db.execute(text("select channel, status, provider_message_id, last_error from outbox_messages")).all()
    assert len(rows) == 4
    assert {(r.channel, r.status, r.last_error) for r in rows} == {("simulated", "delivered", None)}
    assert all(r.provider_message_id.startswith("simulated-") for r in rows)
