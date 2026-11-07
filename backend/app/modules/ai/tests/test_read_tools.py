"""AI-06: read tools on the seeded demo (docs/roadmap/ai/cards/AI-06-read-tools.md). No model calls."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.modules.ai.tests.tool_helpers import Demo, assistant, call, contact_values, ok, seed_demo, table_counts
from app.modules.ai.tools import registry
from app.modules.ai.tools.common import listing, redact_contacts
from app.modules.org.testing import make_business

READ_TOOLS = {
    "search_knowledge": "conversations.thread.read",
    "get_conversation": "conversations.thread.read",
    "find_customers": "crm.customer.read",
    "get_customer": "crm.customer.read",
    "customer_history": "crm.customer.read",
    "list_services": "catalog.item.read",
    "get_service_parts": "catalog.item.read",
    "find_slots": "scheduling.appointment.read",
    "list_appointments": "scheduling.appointment.read",
    "check_stock": "inventory.stock.read",
    "list_low_stock": "inventory.stock.read",
    "list_suppliers_for_item": "purchasing.po.read",
    "get_quote_request": "purchasing.po.read",
    "compare_quotes": "purchasing.po.read",
    "list_late_purchase_orders": "purchasing.po.read",
    "list_overdue_invoices": "sales.document.read",
    "today_summary": "org.business.read",
}


@pytest.fixture
def demo(owner_engine: Engine) -> Demo:
    return seed_demo(owner_engine)


def valid_args(d: Demo) -> dict[str, dict[str, Any]]:
    """One valid call per read tool on the demo business."""
    return {
        "search_knowledge": {"query":"warranty"},
        "get_conversation": {"conversation_id": str(d.conversations["Ada"])},
        "find_customers": {"name": "Dana"},
        "get_customer": {"customer_id": str(d.customers["Ada Example"])},
        "customer_history": {"customer_id": str(d.customers["Dana Example"])},
        "list_services": {"query": "brake"},
        "get_service_parts": {"service_id": str(d.items["Brake service"])},
        "find_slots": {"service_id": str(d.items["Brake service"]), "earliest": "2026-11-10T07:00:00-07:00",
                       "latest": "2026-11-10T17:00:00-07:00"},
        "list_appointments": {"start": "2026-11-09T00:00:00Z", "end": "2026-11-12T00:00:00Z"},
        "check_stock": {"item_ids": [str(d.items["Brake pads (pair)"]), str(d.items["Cable set"])]},
        "list_low_stock": {},
        "list_suppliers_for_item": {"item_id": str(d.items["Brake pads (pair)"])},
        "get_quote_request": {"quote_request_id": str(d.quote_request)},
        "compare_quotes": {"quote_request_id": str(d.quote_request)},
        "list_late_purchase_orders": {},
        "list_overdue_invoices": {},
        "today_summary": {},
    }  # fmt: skip


# ----------------------------------------------------------------------------------------------- catalogue


def test_every_read_tool_is_registered_with_its_permission() -> None:
    read = {t.name: t.permission for t in registry._TOOLS.values() if t.kind == "read"}
    assert read == READ_TOOLS
    assert set(READ_TOOLS.values()) <= AI_SYSTEM_PERMISSIONS


def test_tools_have_no_business_id_argument() -> None:
    for tool in registry._TOOLS.values():
        schema = json.dumps(tool.args_model.model_json_schema())
        assert "business_id" not in tool.args_model.model_fields and "business_id" not in schema, tool.name
        assert tool.args_model.model_config.get("extra") == "forbid", tool.name


def test_tool_catalog_schema_is_json() -> None:
    catalog = registry.tool_catalog(sorted(registry._TOOLS))
    assert json.loads(json.dumps(catalog)) == catalog
    for entry in catalog:
        assert set(entry) == {"name", "description", "arguments"}
        assert entry["arguments"]["type"] == "object" and entry["arguments"]["additionalProperties"] is False
        assert 0 < len(entry["description"]) <= 300


@pytest.mark.parametrize("name", sorted(READ_TOOLS))
def test_read_tool_permission_denied(name: str) -> None:
    permission = READ_TOOLS[name]
    ctx, state = assistant(make_business(), AI_SYSTEM_PERMISSIONS - {permission})
    observation = call(ctx, state, name, {})
    assert observation == {"ok": False, "error": "auth.permission_denied", "detail": permission}
    assert state.evidence == [] and state.read_conversation_ids == set()


def test_unknown_arguments_and_naive_datetimes_are_refused() -> None:
    ctx, state = assistant(make_business())
    extra = call(ctx, state, "list_low_stock", {"business_id": str(uuid.uuid4())})
    assert extra["ok"] is False and extra["error"] == "platform.validation_error"
    naive = call(ctx, state, "find_slots", {"service_id": str(uuid.uuid4()), "earliest": "2026-11-10T07:00:00",
                                            "latest": "2026-11-10T17:00:00Z"})  # fmt: skip
    assert naive["error"] == "platform.validation_error" and "earliest" in naive["detail"]
    for name, args in (("find_customers", {}), ("check_stock", {"item_ids": []}),
                       ("get_conversation", {"conversation_id": "not-a-uuid"})):  # fmt: skip
        assert call(ctx, state, name, args)["error"] == "platform.validation_error", name


# ----------------------------------------------------------------------------------------------- happy paths


def test_get_conversation_reads_thread_and_marks_it_read(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    conversation = demo.conversations["Ada"]
    data = ok(call(ctx, state, "get_conversation", {"conversation_id": str(conversation)}))
    assert data["conversation_id"] == str(conversation) and data["visitor_name"] == "Ada"
    assert data["channel"] == "web_chat" and data["customer_id"] is None
    [message] = data["messages"]
    assert message["author_kind"] == "visitor" and "rear brake is squeaking" in message["body"]
    assert "ada@example.com" not in message["body"] and "[email hidden]" in message["body"]
    assert message["created_at"].endswith("Z")
    assert state.read_conversation_ids == {conversation}
    assert state.evidence[-1].kind == "tool_result" and state.evidence[-1].ref_id == conversation


def test_find_customers_returns_candidates_without_contacts(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "find_customers", {"name": "Dana"}))
    names = [c["display_name"] for c in data["candidates"]]
    assert "Dana Example" in names and len(names) <= 5
    assert set(data["candidates"][0]) == {"customer_id", "display_name", "score", "reason"}
    by_email = ok(call(ctx, state, "find_customers", {"email": "ADA@example.com"}))
    assert by_email["candidates"][0] == {
        "customer_id": str(demo.customers["Ada Example"]), "display_name": "Ada Example",
        "score": by_email["candidates"][0]["score"], "reason": "email",
    }  # fmt: skip


def test_get_customer_returns_contact_types_only(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "get_customer", {"customer_id": str(demo.customers["Ada Example"])}))
    assert data["display_name"] == "Ada Example" and data["contact_types"] == ["email", "phone"]
    assert data["tags"] == ["road-bike"] and data["status"] == "active"


def test_customer_history_lists_timeline(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "customer_history", {"customer_id": str(demo.customers["Dana Example"]),
                                                    "limit": 5}))  # fmt: skip
    summaries = [e["summary"] for e in data["entries"]]
    assert any("brake service" in s for s in summaries) and len(summaries) <= 5
    assert all(e["occurred_at"].endswith("Z") for e in data["entries"])
    assert call(ctx, state, "customer_history", {"customer_id": str(uuid.uuid4()), "limit": 21})["error"] == (
        "platform.validation_error"
    )


def test_list_services_and_get_service_parts(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    services = ok(call(ctx, state, "list_services", {"query": "brake"}))["services"]
    assert services == [{"service_id": str(demo.items["Brake service"]), "name": "Brake service",
                         "duration_minutes": 60, "price": {"minor": 6500, "currency": "USD"},
                         "is_bookable_online": True}]  # fmt: skip
    every = ok(call(ctx, state, "list_services", {"query": ""}))["services"]
    assert {s["name"] for s in every} == {"Brake service", "Standard tune-up", "Flat tyre repair"}

    parts = ok(call(ctx, state, "get_service_parts", {"service_id": str(demo.items["Brake service"])}))
    assert parts["parts"] == [{"item_id": str(demo.items["Brake pads (pair)"]), "item_name": "Brake pads (pair)",
                               "quantity": "1"}]  # fmt: skip
    assert parts["duration_minutes"] == 60 and parts["buffer_minutes"] == 15
    product = call(ctx, state, "get_service_parts", {"service_id": str(demo.items["Brake pads (pair)"])})
    assert product["ok"] is False and product["error"] == "catalog.service_not_found"


def test_find_slots_returns_utc_and_local_label(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "find_slots", valid_args(demo)["find_slots"]))
    first = data["slots"][0]
    # data/fixtures.json expected: the earliest Tuesday-morning brake slot is 11:45 Denver with Jordan.
    assert first == {"starts_at": "2026-11-10T18:45:00Z", "ends_at": "2026-11-10T19:45:00Z",
                     "resource_id": str(demo.resources["Jordan (technician)"]), "resource_name": "Jordan (technician)",
                     "local_label": "Tue 10 Nov 11:45 MST", "resources": []}  # fmt: skip
    assert len(data["slots"]) <= 10 and "window_capped" not in data


def test_find_slots_window_capped(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "find_slots", {"service_id": str(demo.items["Brake service"]),
                                              "earliest": "2026-11-09T00:00:00Z",
                                              "latest": "2027-03-01T00:00:00Z"}))  # fmt: skip
    assert data["window_capped"] is True
    assert data["earliest"] == "2026-11-09T00:00:00Z" and data["latest"] == "2026-11-23T00:00:00Z"
    assert 0 < len(data["slots"]) <= 10
    backwards = call(ctx, state, "find_slots", {"service_id": str(demo.items["Brake service"]),
                                                "earliest": "2026-11-10T00:00:00Z",
                                                "latest": "2026-11-09T00:00:00Z"})  # fmt: skip
    assert backwards["ok"] is False and backwards["error"] == "scheduling.invalid_window"


def test_list_appointments_by_window_and_customer(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "list_appointments", valid_args(demo)["list_appointments"]))
    ben = next(a for a in data["appointments"] if a["customer_name"] == "Ben Example")
    assert ben["service_name"] == "Standard tune-up" and ben["local_label"] == "Tue 10 Nov 10:00 MST"
    assert ben["starts_at"] == "2026-11-10T17:00:00Z" and ben["status"] == "confirmed"
    only_ben = ok(
        call(
            ctx,
            state,
            "list_appointments",
            {**valid_args(demo)["list_appointments"], "customer_id": str(demo.customers["Ben Example"])},
        )
    )
    assert {a["customer_name"] for a in only_ben["appointments"]} == {"Ben Example"}


def test_check_stock_quantities_as_strings(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "check_stock", {"item_ids": [str(demo.items["Brake pads (pair)"]),
                                                            str(demo.items["Brake service"])]}))  # fmt: skip
    [pads] = data["items"]
    assert pads == {"item_id": str(demo.items["Brake pads (pair)"]), "item_name": "Brake pads (pair)", "unit": "each",
                    "on_hand": "3", "reserved": "1", "available": "2", "reorder_point": "2",
                    "below_reorder": False}  # fmt: skip
    assert data["not_stock_tracked"] == [str(demo.items["Brake service"])]


def test_list_low_stock_has_computed_suggestion(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    [cable] = ok(call(ctx, state, "list_low_stock"))["items"]
    assert cable["item_name"] == "Cable set" and cable["suggested"] == "5" and cable["available"] == "1"
    assert cable["reorder_point"] == "2" and cable["target_level"] == "6" and cable["explanation"]


def test_list_suppliers_for_item(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "list_suppliers_for_item", {"item_id": str(demo.items["Brake pads (pair)"])}))
    by_name = {s["supplier_name"]: s for s in data["suppliers"]}
    assert set(by_name) == {"Example Parts Supply", "Example Brake Direct", "Example Cycle Wholesale"}
    assert by_name["Example Brake Direct"]["last_price"] == {"minor": 1400, "currency": "USD"}
    assert by_name["Example Cycle Wholesale"]["on_time_rate"] == "0.333"  # the framework renders floats as text
    assert by_name["Example Parts Supply"]["on_time_rate"] is None and by_name["Example Parts Supply"]["has_email"]


def test_get_quote_request_and_compare_quotes(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    qr = ok(call(ctx, state, "get_quote_request", {"quote_request_id": str(demo.quote_request)}))
    assert (
        qr["status"] == "sent" and qr["lines"][0]["name"] == "Brake pads (pair)" and qr["lines"][0]["quantity"] == "10"
    )
    assert {s["supplier_name"]: s["status"] for s in qr["suppliers"]} == {
        "Example Parts Supply": "invited", "Example Brake Direct": "responded", "Example Cycle Wholesale": "responded",
    }  # fmt: skip
    assert len(qr["quotes"]) == 2 and qr["quotes"][0]["lines"][0]["unit_price"]["currency"] == "USD"

    cmp = ok(call(ctx, state, "compare_quotes", {"quote_request_id": str(demo.quote_request)}))
    first, second = cmp["ranked"]
    # The cheapest quote does not rank first (data/README.md).
    assert first["supplier_name"] == "Example Brake Direct" and first["rank"] == 1
    assert first["total"] == {"minor": 14000, "currency": "USD"} and first["score"] == "0.85"
    assert second["supplier_name"] == "Example Cycle Wholesale" and second["days_late"] == 5
    assert cmp["ineligible"] == []


def test_list_late_purchase_orders(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    [po] = ok(call(ctx, state, "list_late_purchase_orders"))["purchase_orders"]
    assert po["supplier_name"] == "Example Cycle Wholesale" and po["days_late"] == 4
    assert po["open_lines"] == [{"item_id": str(demo.items["Cable set"]), "name": "Cable set", "quantity": "5"}]


def test_list_overdue_invoices_drops_email(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    [invoice] = ok(call(ctx, state, "list_overdue_invoices"))["invoices"]
    assert invoice["bill_to_name"] == "Example Courier Co." and invoice["days_overdue"] == 10
    assert invoice["total"] == {"minor": 19000, "currency": "USD"} and invoice["invoice_version"] >= 1
    assert "bill_to_email" not in invoice and "fleet@example.com" not in json.dumps(invoice)


def test_today_summary(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    data = ok(call(ctx, state, "today_summary"))
    assert data["local_date"] == "2026-11-05" and data["timezone"] == "America/Denver"
    assert data["waiting_conversations"] == {"waiting_on_staff": 3}
    assert [i["item_name"] for i in data["low_stock"]["items"]] == ["Cable set"]
    assert data["agenda"] == {"count": 0, "next": []}


# ----------------------------------------------------------------------------------------------- guarantees


def test_read_tools_cannot_mutate(demo: Demo, owner_engine: Engine) -> None:
    before = table_counts(owner_engine)
    ctx, state = assistant(demo.business)
    after_run = table_counts(owner_engine)  # the run row itself
    for name, args in valid_args(demo).items():
        ok(call(ctx, state, name, args))
    assert table_counts(owner_engine) == after_run
    assert {t: n for t, n in after_run.items() if before[t] != n} == {"ai_runs": before["ai_runs"] + 1}


def test_read_tools_return_no_contact_values(demo: Demo, owner_engine: Engine) -> None:
    secrets = contact_values(owner_engine, demo.business)
    assert {"ada@example.com", "+18015550101", "fleet@example.com"} <= secrets
    ctx, state = assistant(demo.business)
    seen = []
    for name, args in valid_args(demo).items():
        seen.append(json.dumps(call(ctx, state, name, args)))
    for name, args in (("find_customers", {"email": "ada@example.com"}), ("find_customers", {"phone": "+18015550101"}),
                       ("get_customer", {"customer_id": str(demo.customers["Example Courier Co."])}),
                       ("get_conversation", {"conversation_id": str(demo.conversations["Eve"])})):  # fmt: skip
        seen.append(json.dumps(call(ctx, state, name, args)))
    seen.extend(e.excerpt for e in state.evidence)
    text = "\n".join(seen).lower()
    assert [s for s in secrets if s in text] == []


def test_redact_contacts_keeps_dates_times_and_prices() -> None:
    assert redact_contacts("Mail ada@example.com or call +1 801 555 0101 / (801) 555-0104.") == (
        "Mail [email hidden] or call [phone hidden] / [phone hidden]."
    )
    kept = "Tue 10 Nov 10:00, 2026-11-10, PO-2026-0008, $190.00, 10 pairs, 1.000"
    assert redact_contacts(kept) == kept
    assert redact_contacts("x" * 50, 10) == "x" * 9 + "…"


def test_other_business_ids_not_found(demo: Demo) -> None:
    ctx, state = assistant(demo.other)  # Example Garden Store, asking for Example Cycle Works' ids
    cases = {
        "get_conversation": {"conversation_id": str(demo.conversations["Ada"])},
        "get_customer": {"customer_id": str(demo.customers["Ada Example"])},
        "customer_history": {"customer_id": str(demo.customers["Ada Example"])},
        "get_service_parts": {"service_id": str(demo.items["Brake service"])},
        "find_slots": valid_args(demo)["find_slots"],
        "list_appointments": {**valid_args(demo)["list_appointments"],
                              "customer_id": str(demo.customers["Ben Example"])},
        "check_stock": {"item_ids": [str(demo.items["Brake pads (pair)"])]},
        "list_suppliers_for_item": {"item_id": str(demo.items["Brake pads (pair)"])},
        "get_quote_request": {"quote_request_id": str(demo.quote_request)},
        "compare_quotes": {"quote_request_id": str(demo.quote_request)},
    }  # fmt: skip
    for name, args in cases.items():
        observation = call(ctx, state, name, args)
        assert observation["ok"] is False and observation["error"].endswith("not_found"), (name, observation)
    assert state.read_conversation_ids == set()
    # List tools see only the acting business.
    assert ok(call(ctx, state, "list_overdue_invoices"))["invoices"] == []
    assert ok(call(ctx, state, "list_late_purchase_orders"))["purchase_orders"] == []
    assert "Ada Example" not in json.dumps(call(ctx, state, "find_customers", {"name": "Ada"}))


def test_lists_are_cut_to_twenty_items(demo: Demo) -> None:
    assert listing("rows", [{"n": i} for i in range(25)]) == {"rows": [{"n": i} for i in range(20)], "more": True}
    assert listing("rows", [{"n": 1}]) == {"rows": [{"n": 1}]}
    ctx, state = assistant(demo.business)
    many = [str(uuid.uuid4()) for _ in range(21)]
    assert call(ctx, state, "check_stock", {"item_ids": many})["error"] == "platform.validation_error"
    unknown = call(ctx, state, "check_stock", {"item_ids": many[:2]})
    assert unknown["ok"] is False and unknown["error"] == "catalog.item_not_found"
    for _ in range(12):
        ok(call(ctx, state, "list_low_stock"))
    assert len(state.evidence) == 10  # the run keeps the newest 10 citable results
