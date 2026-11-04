"""Sales approval previews: customer and item names, plain quantities, money with its currency (no cents, no ids)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.modules.identity.testing import login_as
from app.platform.db import transaction
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for

from .factories import Shop
from .test_reminders import invoice

UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def preview(shop: Shop, registry: Registry, name: str, payload: BaseModel) -> list[tuple[str, str | None, str | None]]:
    """What the approver sees: validate first (it may capture pricing), then describe, as proposal creation does."""
    spec = registry.executors["sales." + name]
    approver = login_as(shop.app, shop.business, ["admin"])
    ctx = ctx_for(shop.business, approver.permissions, user_id=approver.user_id)
    with transaction(ctx) as db:
        spec.validate(db, ctx, payload)
        lines = [(line.label, line.before, line.after) for line in spec.describe(db, ctx, payload)]
    for label, before, after in lines:
        text = " ".join(part for part in (label, before, after) if part)
        assert not UUID_TEXT.search(text), f"an id leaked into the preview: {text}"
        assert "minor" not in text.lower(), f"minor units leaked into the preview: {text}"
    return lines


def quote_payload(shop: Shop, registry: Registry, lines: list[dict[str, Any]]) -> BaseModel:
    return registry.executors["sales.create_quote"].payload_model.model_validate(
        {"customer_id": shop.customer, "lines": lines}
    )


def test_quote_preview_lists_the_lines_and_totals_in_normal_units(shop: Shop, registry: Registry) -> None:
    payload = quote_payload(
        shop,
        registry,
        [
            {"catalog_item_id": shop.brake, "quantity": "1"},
            {"catalog_item_id": shop.part, "quantity": "2", "tax_rate": "0.1"},
        ],
    )
    assert preview(shop, registry, "create_quote", payload) == [
        ("Customer", None, "Ada Example"),
        ("Brake service", None, "1 × 45.00 USD = 45.00 USD"),
        ("Brake pads", None, "2 × 18.00 USD = 36.00 USD"),
        ("Subtotal", None, "81.00 USD"),
        ("Tax", None, "3.60 USD"),
        ("Total", None, "84.60 USD"),
    ]


def test_order_confirmation_preview_shows_the_customer_lines_and_total(shop: Shop, registry: Registry) -> None:
    quote = shop.create("quotes")
    quote = shop.action("quotes", quote, "send")
    quote = shop.action("quotes", quote, "accept")
    order = shop.action("quotes", quote, "convert")
    payload = registry.executors["sales.confirm_order"].payload_model.model_validate(
        {"order_id": order["id"], "version": order["version"]}
    )
    assert preview(shop, registry, "confirm_order", payload) == [
        ("Customer", None, "Ada Example"),
        ("Order status", "draft", "confirmed"),
        ("Brake service", None, "1 × 45.00 USD = 45.00 USD"),
        ("Brake pads", None, "1 × 18.00 USD = 18.00 USD"),
        ("Subtotal", None, "63.00 USD"),
        ("Tax", None, "0.00 USD"),
        ("Total", None, "63.00 USD"),
    ]


def test_payment_reminder_preview_shows_the_amount_due_in_normal_units(shop: Shop, registry: Registry) -> None:
    row = invoice(shop, "2026-10-01")
    payload = registry.executors["sales.send_payment_reminder"].payload_model.model_validate(
        {"invoice_id": row["id"], "invoice_version": row["version"], "body": "Please pay"}
    )
    spec = registry.executors["sales.send_payment_reminder"]
    ctx = ctx_for(shop.business, ["sales.document.write"])
    with transaction(ctx) as db:
        lines = {line.label: line.after for line in spec.describe(db, ctx, payload)}
    assert lines["Amount due"] == "63.00 USD"
