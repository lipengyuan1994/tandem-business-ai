"""Approval previews read like a person would say them: item names, plain quantities, money with its currency."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.platform.db import transaction
from app.platform.registry import Registry

from ..schemas_quotes import CreatePOFromQuote, RecordSupplierQuote, SendQuoteRequests, SupplierFollowup
from .test_purchasing import action, create
from .test_quotes import context, execute, request_quotes

Preview = list[tuple[str, str | None, str | None]]
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def preview(shop: Any, registry: Registry, name: str, payload: BaseModel) -> Preview:
    """What the approver sees: validate first (it may add a snapshot), then describe, as proposal creation does."""
    spec = registry.executors["purchasing." + name]
    ctx = context(shop)
    with transaction(ctx) as db:
        spec.validate(db, ctx, payload)
        lines = [(line.label, line.before, line.after) for line in spec.describe(db, ctx, payload)]
    for label, before, after in lines:
        text = " ".join(part for part in (label, before, after) if part)
        assert not UUID_TEXT.search(text), f"an id leaked into the preview: {text}"
        assert "minor" not in text.lower(), f"minor units leaked into the preview: {text}"
    return lines


def quote_of(shop: Any, registry: Registry, request_id: UUID) -> UUID:
    payload = RecordSupplierQuote(
        quote_request_id=request_id,
        supplier_id=shop["supplier"]["id"],
        lines=[{"item_id": shop["item"], "unit_price_minor": 1180, "quantity_available": "10"}],
        currency="USD",
        lead_time_days=3,
        source="staff",
    )
    return UUID(execute(shop, registry, "record_supplier_quote", payload)["quote_id"])


def test_quote_request_preview_names_each_item_and_quantity(shop: Any, registry: Registry) -> None:
    payload = SendQuoteRequests(
        lines=[{"item_id": shop["item"], "quantity": "10"}],
        supplier_ids=[shop["supplier"]["id"]],
        needed_by=date(2026, 11, 5),
        reply_by=date(2026, 10, 30),
        message="Example request",
    )
    assert preview(shop, registry, "send_quote_requests", payload) == [
        ("Suppliers", None, "Example Parts"),
        ("Needed by", None, "2026-11-05"),
        ("Reply by", None, "2026-10-30"),
        ("Example pads", None, "10 requested"),
        ("Message", None, "Example request"),
    ]


def test_recorded_quote_preview_shows_item_name_and_price_in_normal_units(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    payload = RecordSupplierQuote(
        quote_request_id=request_id,
        supplier_id=shop["supplier"]["id"],
        lines=[{"item_id": shop["item"], "unit_price_minor": 1180, "quantity_available": "10"}],
        currency="USD",
        lead_time_days=3,
        source="staff",
    )
    assert preview(shop, registry, "record_supplier_quote", payload) == [
        ("Supplier", None, "Example Parts"),
        ("Currency", None, "USD"),
        ("Lead time (days)", None, "3"),
        ("Example pads", None, "11.80 USD each · 10 available"),
        ("Minimum quantity", None, None),
        ("Valid until", None, None),
        ("Notes", None, None),
        ("Source", None, "staff"),
    ]
    only_price = payload.model_copy(
        update={"lines": [payload.lines[0].model_copy(update={"quantity_available": None})], "min_quantity": 5}
    )
    lines = preview(shop, registry, "record_supplier_quote", only_price)
    assert ("Example pads", None, "11.80 USD each") in lines and ("Minimum quantity", None, "5") in lines


def test_purchase_order_from_quote_preview_has_one_line_per_item_and_a_total(shop: Any, registry: Registry) -> None:
    quote_id = quote_of(shop, registry, request_quotes(shop, registry))
    assert preview(shop, registry, "create_po_from_quote", CreatePOFromQuote(quote_id=quote_id, quote_version=1)) == [
        ("Supplier", None, "Example Parts"),
        ("Quote version", None, "1"),
        ("PO status", None, "draft"),
        ("Example pads", None, "10 × 11.80 USD = 118.00 USD"),
        ("Total", None, "118.00 USD"),
        ("Notes", None, None),
    ]


def test_followup_preview_says_what_it_is_about(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    about_request = SupplierFollowup(
        supplier_id=shop["supplier"]["id"], about={"quote_request_id": request_id}, body="Example followup"
    )
    assert preview(shop, registry, "send_supplier_followup", about_request) == [
        ("Supplier", None, "Example Parts"),
        ("About", None, "Quote request for Example pads × 10 · reply by 2026-10-30"),
        ("Message", None, "Example followup"),
    ]
    po = action(shop, action(shop, create(shop), "approve"), "send")
    about_order = SupplierFollowup(
        supplier_id=shop["supplier"]["id"], about={"purchase_order_id": po["id"]}, body="Example chase"
    )
    assert preview(shop, registry, "send_supplier_followup", about_order)[1] == (
        "About",
        None,
        "Purchase order PO-2026-0001 · Example Parts",
    )
