"""Business tax defaults are copied, overridden explicitly, and frozen on document lines."""

from __future__ import annotations

from decimal import Decimal

from .factories import BASE, Shop


def set_tax(shop: Shop, rate: str) -> None:
    current = shop.owner.get("/api/v1/org/settings").json()
    changed = shop.owner.patch(
        "/api/v1/org/settings",
        json={
            "version": current["version"],
            "settings": {**current["settings"], "sales_tax_rate": rate},
        },
    )
    assert changed.status_code == 200, changed.text


def test_tax_defaults_overrides_draft_edits_and_existing_snapshots(shop: Shop) -> None:
    set_tax(shop, "0.0825")
    quote = shop.create(
        "quotes",
        lines=[
            {"catalog_item_id": shop.brake, "quantity": "1"},
            {"catalog_item_id": shop.part, "quantity": "1", "tax_rate": "0"},
            {"name": "Labour", "quantity": "3.335", "unit_price_minor": 300},
        ],
    )
    assert [Decimal(line["tax_rate"]) for line in quote["lines"]] == [Decimal("0.0825"), 0, Decimal("0.0825")]
    assert quote["tax_minor"] == 371 + 83
    assert quote["lines"][2]["line_subtotal_minor"] == 1001
    set_tax(shop, "0.05")
    unchanged = shop.owner.patch(f"{BASE}/quotes/{quote['id']}", json={"version": quote["version"], "notes": "Review"})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["lines"] == quote["lines"]
    edited = shop.owner.patch(
        f"{BASE}/quotes/{quote['id']}",
        json={
            "version": unchanged.json()["version"],
            "lines": [
                {"catalog_item_id": shop.brake, "quantity": "1", "tax_rate": "0.10"},
                {"name": "Labour", "quantity": "1", "unit_price_minor": 1000},
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    draft = edited.json()
    assert [Decimal(line["tax_rate"]) for line in draft["lines"]] == [Decimal("0.10"), Decimal("0.05")]
    assert draft["tax_minor"] == 500
    sent = shop.action("quotes", draft, "send")
    accepted = shop.action("quotes", sent, "accept")
    set_tax(shop, "0.15")
    order = shop.action("quotes", accepted, "convert")
    assert [line["tax_rate"] for line in order["lines"]] == [line["tax_rate"] for line in draft["lines"]]
    assert order["total_minor"] == draft["total_minor"]
    fresh = shop.create()
    assert all(Decimal(line["tax_rate"]) == Decimal("0.15") for line in fresh["lines"])
