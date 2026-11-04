from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from app.modules.identity.testing import login_as
from app.modules.purchasing import permissions as P
from app.modules.purchasing import public, quote_service, tokens
from app.modules.purchasing.models import QuoteRequestSupplier, SupplierQuote
from app.modules.purchasing.schemas_quotes import (
    CreatePOFromQuote,
    RecordSupplierQuote,
    SendQuoteRequests,
    SupplierFollowup,
)
from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, Invalid, NotFound
from app.platform.registry import ErasureTarget, ExecutionMeta, Registry
from app.platform.testing.auth import ctx_for

from .test_purchasing import BASE, action, create, receive


def context(shop: Any) -> Context:
    return ctx_for(shop["business"], P.ALL)


def execute(shop: Any, registry: Registry, name: str, payload: Any) -> dict[str, Any]:
    spec = registry.executors["purchasing." + name]
    with transaction(context(shop)) as db:
        spec.validate(db, context(shop), payload)
        assert spec.describe(db, context(shop), payload)
        return spec.run(db, context(shop), payload, ExecutionMeta(uuid4(), uuid4(), str(uuid4())))


def request_quotes(shop: Any, registry: Registry, suppliers: list[str] | None = None) -> UUID:
    result = execute(
        shop,
        registry,
        "send_quote_requests",
        SendQuoteRequests(
            lines=[{"item_id": shop["item"], "quantity": "10"}],
            supplier_ids=suppliers or [shop["supplier"]["id"]],
            needed_by=date(2026, 11, 5),
            reply_by=date(2026, 10, 30),
            message="Example request",
        ),
    )
    return UUID(result["quote_request_id"])


def record(shop: Any, registry: Registry, request_id: UUID, supplier_id: str | None = None, **extra: Any) -> UUID:
    payload = RecordSupplierQuote(
        quote_request_id=request_id,
        supplier_id=supplier_id or shop["supplier"]["id"],
        lines=[{"item_id": shop["item"], "unit_price_minor": 100, "quantity_available": "10"}],
        currency="USD",
        lead_time_days=3,
        source="staff",
        **extra,
    )
    return UUID(execute(shop, registry, "record_supplier_quote", payload)["quote_id"])


def token_for(owner_engine: Engine, supplier: str | None = None) -> str:
    with owner_engine.connect() as db:
        rows = db.execute(
            text("select recipient, variables from outbox_messages where template_key='quote_request' order by id")
        ).all()
    return str(
        next(r.variables["quote_url"].rsplit("/", 1)[1] for r in rows if supplier is None or r.recipient == supplier)
    )


def form(shop: Any) -> TestClient:
    return TestClient(shop["app"], base_url="https://testserver")


def second_supplier(shop: Any) -> str:
    result = shop["client"].post(BASE + "/suppliers", json={"name": "Example Second", "email": "second@example.com"})
    assert result.status_code == 201
    return str(result.json()["id"])


def test_send_quote_requests_emails_each_supplier_once_with_signed_link(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    other = second_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], other])
    with owner_engine.connect() as db:
        messages = (
            db.execute(text("select variables from outbox_messages where template_key='quote_request'")).scalars().all()
        )
        hashes = set(db.execute(text("select token_hash from quote_request_suppliers")).scalars())
    assert len(messages) == 2
    for message in messages:
        assert message["quote_url"].startswith("https://") and "/q/" in message["quote_url"]
        token = message["quote_url"].rsplit("/", 1)[1]
        assert tokens.read_token(token)[1] == request_id
        assert hashlib.sha256(token.encode()).hexdigest() in hashes
    missing = shop["client"].post(BASE + "/suppliers", json={"name": "Example No Email"}).json()["id"]
    with pytest.raises(Invalid):
        request_quotes(shop, registry, [missing])


def test_quote_token_is_single_supplier_and_expires(shop: Any, registry: Registry, owner_engine: Engine) -> None:
    other = second_supplier(shop)
    request_quotes(shop, registry, [shop["supplier"]["id"], other])
    token = token_for(owner_engine, "parts@example.com")
    c = form(shop)
    assert c.get("/api/v1/public/quotes/" + token).status_code == 200
    assert c.get("/api/v1/public/quotes/" + token[:-1] + ("A" if token[-1] != "A" else "B")).status_code == 422
    assert str(tokens.read_token(token)[2]) == shop["supplier"]["id"]
    clock.set_clock(lambda: datetime(2026, 11, 7, 7, tzinfo=UTC))  # after Nov 6 in Denver
    assert c.get("/api/v1/public/quotes/" + token).status_code == 422


def test_public_quote_post_creates_quote_and_marks_responded(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    request_id = request_quotes(shop, registry)
    c = form(shop)
    url = "/api/v1/public/quotes/" + token_for(owner_engine)
    payload = {
        "lines": [{"item_id": shop["item"], "unit_price_minor": 99}],
        "lead_time_days": 2,
        "valid_until": "2026-11-05",
        "notes": "Example offer",
    }
    first = c.post(url, json=payload)
    assert first.status_code == 200, first.text
    payload["lead_time_days"] = 4
    assert c.post(url, json=payload).status_code == 200
    with transaction(context(shop)) as db:
        request = public.get_quote_request(db, context(shop), request_id)
        assert request.suppliers[0].status == "responded"
        assert len(request.quotes) == 1 and request.quotes[0].source == "form"
        assert request.quotes[0].version == 2 and request.quotes[0].lead_time_days == 4


def test_public_quote_rate_limited(shop: Any, registry: Registry, owner_engine: Engine) -> None:
    request_quotes(shop, registry)
    c, url = form(shop), "/api/v1/public/quotes/" + token_for(owner_engine)
    for _ in range(10):
        assert c.post(url, json={"declined": True}).status_code == 200
    refused = c.post(url, json={"declined": True})
    assert refused.status_code == 429
    # F7: the standard header in whole seconds, the same number as the body, within the 60 s window of this limit.
    assert refused.headers["Retry-After"] == str(refused.json()["retry_after_seconds"])
    assert 1 <= int(refused.headers["Retry-After"]) <= 60


def test_public_quote_validation_errors_name_fields_not_internal_classes(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    """F6: the body is a union of two models; Pydantic's names for them must not reach a supplier's browser."""
    request_quotes(shop, registry)
    c, url = form(shop), "/api/v1/public/quotes/" + token_for(owner_engine)
    line = {"item_id": shop["item"], "unit_price_minor": 100}
    bodies = {
        "bad price": {"lines": [{**line, "unit_price_minor": -5}], "lead_time_days": 2},
        "bad lead time": {"lines": [line], "lead_time_days": "soon"},
        "empty": {},
        "same item twice": {"lines": [line, line], "lead_time_days": 2},
        "unknown field": {"lines": [line], "lead_time_days": 2, "colour": "red"},
    }
    seen: dict[str, set[str]] = {}
    for name, body in bodies.items():
        response = c.post(url, json=body)
        assert response.status_code == 422 and response.json()["code"] == "platform.validation_error", name
        errors = response.json()["errors"]
        seen[name] = {e["field"] for e in errors}
        for field in seen[name]:  # JSON field names and list positions only; "" for a rule about the whole body
            assert field == "" or re.fullmatch(r"[a-z][a-z0-9_]*(\.([a-z][a-z0-9_]*|\d+))*", field), (name, field)
        assert len({(e["field"], e["code"]) for e in errors}) == len(errors), name
    # The names the supplier form looks for (frontend/src/widget/QuoteFields.tsx) are there.
    assert "lines.0.unit_price_minor" in seen["bad price"] and "lead_time_days" in seen["bad lead time"]
    assert "colour" in seen["unknown field"] and "" in seen["same item twice"]
    # Not a word of our code in the answer, wherever it could hide.
    leaked = c.post(url, json=bodies["bad price"]).text
    assert not re.search(r"DeclineQuote|PublicQuoteInput|QuoteInput|function-|unique_lines", leaked)


def test_compare_quotes_formula_components_and_tie_break(shop: Any, registry: Registry) -> None:
    other = second_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], other])
    first, second = record(shop, registry, request_id), record(shop, registry, request_id, other)
    with transaction(context(shop)) as db:
        comparison = public.compare_quotes(db, context(shop), request_id)
    assert {q.quote_id for q in comparison.ranked} == {first, second}
    q = comparison.ranked[0]
    assert q.total_minor == 1000 and q.price_score == 1 and q.lead_score == 1 and q.reliability == 0.8
    assert q.score == 0.97
    with transaction(context(shop)) as db:
        row = db.get(SupplierQuote, second)
        assert row is not None
        row.lead_time_days = 2
        db.flush()
        tied = public.compare_quotes(db, context(shop), request_id).ranked
        assert tied[0].score == tied[1].score and tied[0].quote_id == second
    with transaction(context(shop)) as db:
        row = db.get(SupplierQuote, first)
        assert row is not None
        row.lead_time_days = 20
    with transaction(context(shop)) as db:
        comparison = public.compare_quotes(db, context(shop), request_id)
    assert comparison.ranked[0].quote_id == second
    assert comparison.ranked[1].lead_score == pytest.approx(1 - 12 / 14)
    with transaction(context(shop)) as db:
        db.execute(text("update quote_request_lines set quantity=1"))
        db.execute(text("update supplier_quote_lines set unit_price_minor=1000 where quote_id=:q"), {"q": first})
        db.execute(text("update supplier_quote_lines set unit_price_minor=1714 where quote_id=:q"), {"q": second})
        row = db.get(SupplierQuote, first)
        assert row is not None
        row.lead_time_days = 30
        db.flush()
        tied = public.compare_quotes(db, context(shop), request_id).ranked
        assert tied[0].score == tied[1].score == 0.72 and tied[0].quote_id == first


def test_compare_eligibility_reasons_currency_validity_quantity_total(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    quote_id = record(shop, registry, request_id)
    changes = [
        ("currency", "EUR", "other_currency"),
        ("valid_until", date(2026, 10, 27), "expired"),
        ("min_quantity", Decimal(11), "quantity"),
    ]
    for field, value, reason in changes:
        with transaction(context(shop)) as db:
            row = db.get(SupplierQuote, quote_id)
            assert row is not None
            original = getattr(row, field)
            setattr(row, field, value)
            db.flush()
            result = public.compare_quotes(db, context(shop), request_id)
            assert not result.ranked and reason in result.ineligible[0].reasons
            setattr(row, field, original)
    with transaction(context(shop)) as db:
        db.execute(text("update supplier_quote_lines set quantity_available=9"))
        assert "quantity" in public.compare_quotes(db, context(shop), request_id).ineligible[0].reasons
        db.execute(text("update supplier_quote_lines set quantity_available=null, unit_price_minor=0"))
        assert "invalid_total" in public.compare_quotes(db, context(shop), request_id).ineligible[0].reasons
        db.execute(text("delete from supplier_quote_lines"))
        assert "incomplete" in public.compare_quotes(db, context(shop), request_id).ineligible[0].reasons


def test_quote_received_event_all_answered(shop: Any, registry: Registry, owner_engine: Engine) -> None:
    other = second_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], other])
    record(shop, registry, request_id)
    assert (
        form(shop)
        .post("/api/v1/public/quotes/" + token_for(owner_engine, "second@example.com"), json={"declined": True})
        .status_code
        == 200
    )
    with owner_engine.connect() as db:
        payloads = (
            db.execute(text("select payload from domain_events where type='purchasing.quote_received' order by id"))
            .scalars()
            .all()
        )
    assert [p["all_answered"] for p in payloads] == [False, True]
    assert payloads[-1]["declined"] is True


def test_list_quote_requests_due_and_unanswered_invites(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    ctx = context(shop)
    with transaction(ctx) as db:
        assert public.list_quote_requests_due(db, ctx) == []
        invites = public.list_unanswered_invites(db, ctx, as_of=date(2026, 10, 31))
        assert len(invites) == 1 and invites[0].days_overdue == 1
        assert public.list_quote_requests_due(db, ctx, as_of=date(2026, 10, 31))[0].quote_request_id == request_id
    record(shop, registry, request_id)
    with transaction(ctx) as db:
        assert public.list_unanswered_invites(db, ctx) == []
        assert public.list_quote_requests_due(db, ctx)[0].quote_request_id == request_id


def test_reliability_uses_expected_delivery_and_defaults_for_new_suppliers(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    ctx = context(shop)
    with transaction(ctx) as db:
        assert public.list_suppliers_for_item(db, ctx, UUID(shop["item"]))[0].on_time_rate is None
    for i in range(3):
        po = action(shop, action(shop, create(shop), "approve"), "send")
        with owner_engine.begin() as db:
            db.execute(
                text("update purchase_orders set expected_delivery_date=:d where id=:id"),
                {"d": date(2026, 10, 28 if i < 2 else 27), "id": po["id"]},
            )
        assert receive(shop, po, "10").status_code == 201
    request_id = request_quotes(shop, registry)
    record(shop, registry, request_id)
    with transaction(ctx) as db:
        option = public.list_suppliers_for_item(db, ctx, UUID(shop["item"]))[0]
        assert option.orders_count == 3 and option.on_time_rate == pytest.approx(2 / 3)
        assert public.compare_quotes(db, ctx, request_id).ranked[0].reliability == pytest.approx(2 / 3)


def test_create_po_from_quote_creates_draft_po_needing_approval(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    quote_id = record(shop, registry, request_id)
    result = execute(shop, registry, "create_po_from_quote", CreatePOFromQuote(quote_id=quote_id, quote_version=1))
    po = shop["client"].get(BASE + "/purchase-orders/" + result["purchase_order_id"]).json()
    assert po["status"] == "draft" and po["number"] is None and po["total_minor"] == 1000
    assert (
        shop["client"]
        .post(BASE + "/purchase-orders/" + po["id"] + "/send", json={"version": po["version"]})
        .status_code
        == 409
    )
    with transaction(context(shop)) as db:
        assert public.list_quote_requests_due(db, context(shop)) == []
    with pytest.raises(Conflict):
        execute(shop, registry, "create_po_from_quote", CreatePOFromQuote(quote_id=quote_id, quote_version=1))


def test_expected_delivery_date_set_on_send(shop: Any) -> None:
    po = action(shop, action(shop, create(shop), "approve"), "send")
    assert po["sent_at"] == "2026-10-28T12:00:00Z" and po["expected_delivery_date"] == "2026-10-31"
    changed = shop["client"].patch(
        BASE + "/purchase-orders/" + po["id"], json={"version": po["version"], "expected_delivery_date": "2026-11-02"}
    )
    assert changed.status_code == 200, changed.text


def test_list_late_purchase_orders_business_timezone(shop: Any) -> None:
    po = action(shop, action(shop, create(shop), "approve"), "send")
    clock.set_clock(lambda: datetime(2026, 11, 1, 1, tzinfo=UTC))  # still Oct 31 Denver
    with transaction(context(shop)) as db:
        assert public.list_late_purchase_orders(db, context(shop)) == []
        late = public.list_late_purchase_orders(db, context(shop), as_of=date(2026, 11, 1))
        assert late[0].id == UUID(po["id"]) and late[0].days_late == 1
        assert late[0].open_lines[0].quantity == 10


def test_followup_email_through_outbox(shop: Any, registry: Registry, owner_engine: Engine) -> None:
    request_id = request_quotes(shop, registry)
    result = execute(
        shop,
        registry,
        "send_supplier_followup",
        SupplierFollowup(
            supplier_id=shop["supplier"]["id"], about={"quote_request_id": request_id}, body="Example followup"
        ),
    )
    with transaction(context(shop)) as db:
        invite = db.scalar(select(QuoteRequestSupplier))
        assert invite is not None and invite.last_followup_at == clock.now()
    with owner_engine.connect() as db:
        row = (
            db.execute(text("select * from outbox_messages where id=:id"), {"id": result["outbox_id"]}).mappings().one()
        )
        assert row["template_key"] == "supplier_followup" and row["status"] == "pending"
    po = action(shop, action(shop, create(shop), "approve"), "send")
    execute(
        shop,
        registry,
        "send_supplier_followup",
        SupplierFollowup(
            supplier_id=shop["supplier"]["id"], about={"purchase_order_id": po["id"]}, body="Example chase"
        ),
    )
    assert (
        shop["client"].get(BASE + "/purchase-orders/" + po["id"]).json()["last_followup_at"] == "2026-10-28T12:00:00Z"
    )


def test_cross_business_token_or_ids_rejected(
    shop: Any, registry: Registry, other_business: UUID, owner_engine: Engine
) -> None:
    request_id = request_quotes(shop, registry)
    other = ctx_for(other_business, P.ALL)
    with transaction(other) as db, pytest.raises(NotFound):
        public.get_quote_request(db, other, request_id)
    original = token_for(owner_engine)
    forged = tokens.make_token(
        other_business, request_id, UUID(shop["supplier"]["id"]), datetime(2026, 11, 7, tzinfo=UTC)
    )
    assert form(shop).get("/api/v1/public/quotes/" + forged).status_code == 404
    assert form(shop).get("/api/v1/public/quotes/" + original).status_code == 200
    clock.set_clock(lambda: datetime(2026, 11, 8, tzinfo=UTC))
    with transaction(other) as db:
        quote_service.expire_invites(db, other, {})
    with transaction(context(shop)) as db:
        assert public.get_quote_request(db, context(shop), request_id).suppliers[0].status == "invited"
        quote_service.expire_invites(db, context(shop), {})
        assert public.get_quote_request(db, context(shop), request_id).suppliers[0].status == "expired"


def test_erasure_and_export_cover_new_tables(shop: Any, registry: Registry) -> None:
    request_id = request_quotes(shop, registry)
    record(shop, registry, request_id, notes="Example Person mentioned")
    with transaction(context(shop)) as db:
        registry.erasure_handlers["purchasing"](db, context(shop), ErasureTarget(uuid4(), "Example Person", (), ()))
        exported = registry.export_handlers["purchasing"](db, context(shop))
    assert {
        "quote_requests",
        "quote_request_lines",
        "quote_request_suppliers",
        "supplier_quotes",
        "supplier_quote_lines",
    } <= set(exported)
    assert "token_hash" not in exported["quote_request_suppliers"][0]
    assert exported["supplier_quotes"][0]["notes"] == "[erased] mentioned"


def test_staff_writes_are_pending_proposals_and_require_permission(shop: Any, registry: Registry) -> None:
    payload = {
        "lines": [{"item_id": shop["item"], "quantity": "10"}],
        "supplier_ids": [shop["supplier"]["id"]],
        "needed_by": "2026-11-05",
        "reply_by": "2026-10-30",
    }
    result = shop["client"].post(BASE + "/quote-requests", json=payload, headers={"Idempotency-Key": "example-request"})
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "pending_review"
    assert (
        shop["client"]
        .post(BASE + "/quote-requests", json=payload, headers={"Idempotency-Key": "example-request"})
        .json()
        == result.json()
    )
    assert shop["client"].get(BASE + "/quote-requests").json()["items"] == []
    auditor = login_as(shop["app"], shop["business"], ["auditor"])
    assert auditor.post(BASE + "/quote-requests", json=payload).status_code == 403


def test_request_execution_replay_and_rollback_are_atomic(
    shop: Any, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = SendQuoteRequests(
        lines=[{"item_id": shop["item"], "quantity": "10"}],
        supplier_ids=[shop["supplier"]["id"]],
        needed_by=date(2026, 11, 5),
        reply_by=date(2026, 10, 30),
    )
    spec = registry.executors["purchasing.send_quote_requests"]
    meta = ExecutionMeta(uuid4(), uuid4(), "replay-request")
    ctx = context(shop)
    with transaction(ctx) as db:
        first = spec.run(db, ctx, p, meta)
    with transaction(ctx) as db:
        assert spec.run(db, ctx, p, meta) == first
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from quote_requests")) == 1
        assert db.scalar(text("select count(*) from outbox_messages where template_key='quote_request'")) == 1
    original = quote_service.notifications.enqueue_message

    def fail_after_enqueue(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise RuntimeError("transaction rollback")

    monkeypatch.setattr(quote_service.notifications, "enqueue_message", fail_after_enqueue)
    with pytest.raises(RuntimeError, match="transaction rollback"), transaction(ctx) as db:
        spec.run(db, ctx, p, ExecutionMeta(uuid4(), uuid4(), "rollback-request"))
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from quote_requests")) == 1
        assert db.scalar(text("select count(*) from quote_request_suppliers")) == 1
        assert db.scalar(text("select count(*) from outbox_messages where template_key='quote_request'")) == 1


def test_supplier_form_cannot_spoof_supplier_and_decline_removes_ranking(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    other = second_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], other])
    url = "/api/v1/public/quotes/" + token_for(owner_engine, "parts@example.com")
    c = form(shop)
    payload = {"lines": [{"item_id": shop["item"], "unit_price_minor": 100}], "lead_time_days": 2}
    assert c.post(url, json={**payload, "supplier_id": other}).status_code == 422
    assert c.post(url, json=payload).status_code == 200
    with transaction(context(shop)) as db:
        assert len(public.compare_quotes(db, context(shop), request_id).ranked) == 1
    assert c.post(url, json={"declined": True}).status_code == 200
    with transaction(context(shop)) as db:
        assert public.compare_quotes(db, context(shop), request_id).ranked == []
    assert c.post(url, json=payload).status_code == 200
    with transaction(context(shop)) as db:
        request = public.get_quote_request(db, context(shop), request_id)
        assert len(request.quotes) == 1 and str(request.quotes[0].supplier_id) == shop["supplier"]["id"]
        assert request.quotes[0].version == 3


def test_concurrent_supplier_answers_publish_final_all_answered(
    shop: Any, registry: Registry, owner_engine: Engine
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    other = second_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], other])
    barrier = threading.Barrier(2)

    def answer(supplier_id: str) -> None:
        barrier.wait(timeout=10)
        record(shop, registry, request_id, supplier_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(answer, [shop["supplier"]["id"], other]))
    with owner_engine.connect() as db:
        answers = (
            db.execute(text("select payload from domain_events where type='purchasing.quote_received' order by id"))
            .scalars()
            .all()
        )
    assert [p["all_answered"] for p in answers] == [False, True]


def test_staff_proposals_execute_through_normal_approval_engine(shop: Any, registry: Registry) -> None:
    requester = login_as(shop["app"], shop["business"], ["inventory_manager"])
    approver = login_as(shop["app"], shop["business"], ["admin"])

    def approve(result: Any) -> None:
        assert result.status_code == 201, result.text
        proposal_id = result.json()["id"]
        detail = approver.get("/api/v1/approvals/proposals/" + proposal_id).json()
        revision = detail["current_revision"]
        decision = approver.post(
            "/api/v1/approvals/proposals/" + proposal_id + "/decisions",
            json={"decision": "approve", "revision_id": revision["id"], "payload_hash": revision["payload_hash"]},
        )
        assert decision.status_code == 200, decision.text
        handler = registry.jobs["approvals.execute"].handler
        ctx = context(shop)
        for _ in range(2):
            with transaction(ctx) as db:
                handler(db, ctx, {"proposal_id": proposal_id, "revision_id": revision["id"]})
            assert approver.get("/api/v1/approvals/proposals/" + proposal_id).json()["status"] == "succeeded"

    result = requester.post(
        BASE + "/quote-requests",
        json={
            "lines": [{"item_id": shop["item"], "quantity": "10"}],
            "supplier_ids": [shop["supplier"]["id"]],
            "needed_by": "2026-11-05",
            "reply_by": "2026-10-30",
        },
    )
    approve(result)
    requests = requester.get(BASE + "/quote-requests").json()["items"]
    assert len(requests) == 1
    request_id = requests[0]["id"]
    approve(
        requester.post(
            BASE + "/supplier-followups",
            json={
                "supplier_id": shop["supplier"]["id"],
                "about": {"quote_request_id": request_id},
                "body": "Example reminder",
            },
        )
    )
    approve(
        requester.post(
            BASE + "/quote-requests/" + request_id + "/quotes",
            json={
                "quote_request_id": request_id,
                "supplier_id": shop["supplier"]["id"],
                "lines": [{"item_id": shop["item"], "unit_price_minor": 123}],
                "lead_time_days": 5,
                "currency": "USD",
                "source": "staff",
            },
        )
    )
    comparison = requester.get(BASE + "/quote-requests/" + request_id + "/comparison").json()
    quote = comparison["ranked"][0]
    approve(
        requester.post(
            BASE + "/purchase-orders/from-quote",
            json={"quote_id": quote["quote_id"], "quote_version": quote["quote_version"]},
        )
    )
    po = requester.get(BASE + "/purchase-orders").json()["items"][0]
    assert po["status"] == "draft" and po["number"] is None
    shop["client"] = approver
    po = action(shop, action(shop, po, "approve"), "send")
    assert po["expected_delivery_date"] == "2026-11-02"


def test_migration_downgrade_upgrade_and_forced_rls(owner_engine: Engine) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["url"] = str(owner_engine.url.render_as_string(hide_password=False))
    command.downgrade(cfg, "20261031_1100_invoice_reminders")
    with owner_engine.connect() as db:
        assert db.scalar(text("select to_regclass('quote_requests')")) is None
        assert db.scalar(text("select to_regclass('purchase_orders')")) is not None
    command.upgrade(cfg, "head")
    with owner_engine.connect() as db:
        rows = db.execute(
            text(
                "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname in "
                "('quote_requests','quote_request_lines','quote_request_suppliers','supplier_quotes','supplier_quote_lines')"
            )
        ).all()
    assert len(rows) == 5 and all(r.relrowsecurity and r.relforcerowsecurity for r in rows)


def test_quote_public_reads_and_executors_enforce_permissions(shop: Any, registry: Registry) -> None:
    from app.platform.errors import Forbidden

    request_id = request_quotes(shop, registry)
    denied = ctx_for(shop["business"])
    calls = [
        lambda db: public.get_quote_request(db, denied, request_id),
        lambda db: public.compare_quotes(db, denied, request_id),
        lambda db: public.list_suppliers_for_item(db, denied, UUID(shop["item"])),
        lambda db: public.list_late_purchase_orders(db, denied),
        lambda db: public.list_quote_requests_due(db, denied),
        lambda db: public.list_unanswered_invites(db, denied),
    ]
    for call in calls:
        with pytest.raises(Forbidden), transaction(denied) as db:
            call(db)
    payloads = [
        (
            "send_quote_requests",
            SendQuoteRequests(
                lines=[{"item_id": shop["item"], "quantity": "10"}],
                supplier_ids=[shop["supplier"]["id"]],
                needed_by=date(2026, 11, 5),
                reply_by=date(2026, 10, 30),
            ),
        ),
        (
            "record_supplier_quote",
            RecordSupplierQuote(
                quote_request_id=request_id,
                supplier_id=shop["supplier"]["id"],
                lines=[{"item_id": shop["item"], "unit_price_minor": 100}],
                currency="USD",
                lead_time_days=3,
            ),
        ),
        (
            "send_supplier_followup",
            SupplierFollowup(
                supplier_id=shop["supplier"]["id"], about={"quote_request_id": request_id}, body="Example followup"
            ),
        ),
        ("create_po_from_quote", CreatePOFromQuote(quote_id=uuid4(), quote_version=1)),
    ]
    for name, payload in payloads:
        with pytest.raises(Forbidden), transaction(denied) as db:
            registry.executors["purchasing." + name].run(
                db, denied, payload, ExecutionMeta(uuid4(), uuid4(), "denied-permission")
            )


def test_reliability_limits_history_to_last_ten_completions(shop: Any, registry: Registry) -> None:
    from datetime import timedelta

    from app.modules.purchasing.models import GoodsReceipt, PurchaseOrder

    ctx = context(shop)
    with transaction(ctx) as db:
        for index in range(12):
            completed = datetime(2026, 10, 1, 12, tzinfo=UTC) + timedelta(days=index)
            po = PurchaseOrder(
                business_id=shop["business"],
                supplier_id=UUID(shop["supplier"]["id"]),
                supplier_name="Example Parts",
                currency="USD",
                status="received",
                number=f"PO-2026-{index + 1:04d}",
                approved_date=date(2026, 9, 20),
                expected_delivery_date=completed.date() - timedelta(days=index < 2),
            )
            db.add(po)
            db.flush()
            db.add(GoodsReceipt(business_id=shop["business"], purchase_order_id=po.id, created_at=completed))
    with transaction(ctx) as db:
        supplier = public.list_suppliers_for_item(db, ctx, UUID(shop["item"]))[0]
        assert supplier.orders_count == 10 and supplier.on_time_rate == 1


def test_po_from_quote_rechecks_currency_at_creation(
    shop: Any, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.purchasing import service

    request_id = request_quotes(shop, registry)
    quote_id = record(shop, registry, request_id)
    currencies = iter(["USD", "EUR"])
    monkeypatch.setattr(service.catalog, "get_default_currency", lambda db, ctx: next(currencies))
    ctx = context(shop)
    with pytest.raises(Conflict, match=r"purchasing\.preview_changed"), transaction(ctx) as db:
        registry.executors["purchasing.create_po_from_quote"].run(
            db,
            ctx,
            CreatePOFromQuote(quote_id=quote_id, quote_version=1),
            ExecutionMeta(uuid4(), uuid4(), "currency-race"),
        )
    assert shop["client"].get(BASE + "/purchase-orders").json()["items"] == []


def test_compare_quotes_rounds_exact_two_thirds_reliability_half_up(shop: Any, registry: Registry) -> None:
    from app.modules.purchasing.models import GoodsReceipt, PurchaseOrder

    other = second_supplier(shop)
    request_id = request_quotes(shop, registry, [shop["supplier"]["id"], other])
    expensive = record(shop, registry, request_id)
    cheap = record(shop, registry, request_id, other)
    ctx = context(shop)
    with transaction(ctx) as db:
        db.execute(text("update quote_request_lines set quantity=1"))
        for quote_id, price in [(expensive, 400), (cheap, 267)]:
            db.execute(
                text("update supplier_quote_lines set unit_price_minor=:price where quote_id=:id"),
                {"id": quote_id, "price": price},
            )
        for index in range(3):
            po = PurchaseOrder(
                business_id=ctx.business_id,
                supplier_id=UUID(shop["supplier"]["id"]),
                supplier_name="Example Parts",
                currency="USD",
                status="received",
                number=f"PO-2026-{index + 1:04d}",
                approved_date=date(2026, 10, 20),
                expected_delivery_date=date(2026, 10, 28 if index < 2 else 27),
            )
            db.add(po)
            db.flush()
            db.add(GoodsReceipt(business_id=ctx.business_id, purchase_order_id=po.id, created_at=clock.now()))
    with transaction(ctx) as db:
        ranked = public.compare_quotes(db, ctx, request_id).ranked
        quote = next(q for q in ranked if q.quote_id == expensive)
        assert quote.price_score == 267 / 400 and quote.reliability == 2 / 3 and quote.lead_score == 1
        assert quote.score == 0.751  # exact weighted sum is 0.7505, not 0.750499999999999990


@pytest.mark.parametrize("aggregate", [False, True])
def test_conversion_proposal_rejects_unrepresentable_po_amounts(
    shop: Any, registry: Registry, owner_engine: Engine, aggregate: bool
) -> None:
    items = [shop["item"]]
    if aggregate:
        result = shop["client"].post(
            "/api/v1/catalog/items", json={"name": "Example tubes", "kind": "product", "is_stock_tracked": True}
        )
        assert result.status_code == 201
        items.append(result.json()["id"])
    quantity = "600" if aggregate else "2000"
    result = execute(
        shop,
        registry,
        "send_quote_requests",
        SendQuoteRequests(
            lines=[{"item_id": item, "quantity": quantity} for item in items],
            supplier_ids=[shop["supplier"]["id"]],
            needed_by=date(2026, 11, 5),
            reply_by=date(2026, 10, 30),
        ),
    )
    request_id = UUID(result["quote_request_id"])
    response = form(shop).post(
        "/api/v1/public/quotes/" + token_for(owner_engine),
        json={"lines": [{"item_id": item, "unit_price_minor": 10**12} for item in items], "lead_time_days": 1},
    )
    assert response.status_code == 200, response.text
    with transaction(context(shop)) as db:
        quote = public.get_quote_request(db, context(shop), request_id).quotes[0]
    response = shop["client"].post(
        BASE + "/purchase-orders/from-quote", json={"quote_id": str(quote.id), "quote_version": quote.version}
    )
    assert response.status_code == 422 and response.json()["code"] == "purchasing.amount_too_large"
    with owner_engine.connect() as db:
        assert (
            db.scalar(text("select count(*) from action_proposals where action_type='purchasing.create_po_from_quote'"))
            == 0
        )
        assert db.scalar(text("select count(*) from purchase_orders")) == 0


@pytest.mark.parametrize("location", ["request", "quote", "followup_only"])
def test_customer_erasure_scrubs_linked_procurement_outbox_without_changing_supplier_credentials(
    shop: Any, registry: Registry, owner_engine: Engine, other_business: UUID, location: str
) -> None:
    name = "Synthetic Person"
    customer = shop["client"].post(
        "/api/v1/crm/customers",
        json={"display_name": name, "contact_points": [{"type": "email", "value": "person@example.com"}]},
    )
    assert customer.status_code == 201, customer.text
    result = execute(
        shop,
        registry,
        "send_quote_requests",
        SendQuoteRequests(
            lines=[{"item_id": shop["item"], "quantity": "10"}],
            supplier_ids=[shop["supplier"]["id"]],
            needed_by=date(2026, 11, 5),
            reply_by=date(2026, 10, 30),
            message=f"Parts for {name}" if location == "request" else "Example request",
        ),
    )
    request_id = UUID(result["quote_request_id"])
    execute(
        shop,
        registry,
        "send_supplier_followup",
        SupplierFollowup(
            supplier_id=shop["supplier"]["id"],
            about={"quote_request_id": request_id},
            body=f"Please help {name}" if location == "followup_only" else "Example followup",
        ),
    )
    if location == "quote":
        quote_id = record(shop, registry, request_id, notes=f"Parts for {name}")
        result = execute(shop, registry, "create_po_from_quote", CreatePOFromQuote(quote_id=quote_id, quote_version=1))
        po = shop["client"].get(BASE + "/purchase-orders/" + result["purchase_order_id"]).json()
        po = action(shop, action(shop, po, "approve"), "send")
        execute(
            shop,
            registry,
            "send_supplier_followup",
            SupplierFollowup(
                supplier_id=shop["supplier"]["id"], about={"purchase_order_id": po["id"]}, body=f"Arrival for {name}"
            ),
        )
    unrelated = request_quotes(shop, registry)
    with owner_engine.connect() as db:
        before = (
            db.execute(text("select id, variables from outbox_messages where related_id=:id"), {"id": request_id})
            .mappings()
            .all()
        )
    with owner_engine.begin() as db:
        # The queue has no RLS. An identical related ID in another tenant must remain untouched.
        db.execute(
            text(
                "insert into outbox_messages (id,business_id,channel,recipient,template_key,variables,"
                "idempotency_key,related_type,related_id,status) "
                "values (:id,:b,'email','other@example.com','supplier_followup',"
                "jsonb_build_object('body',cast(:name as text)),"
                "'other-procurement-copy','quote_request',:ref,'pending')"
            ),
            {"id": uuid4(), "b": other_business, "name": name, "ref": request_id},
        )
    response = shop["client"].post(
        "/api/v1/admin/erasures", json={"customer_id": customer.json()["id"], "confirm_name": name}
    )
    assert response.status_code == 200, response.text
    with owner_engine.connect() as db:
        rows = (
            db.execute(
                text(
                    "select * from outbox_messages where business_id=:b "
                    "and template_key in ('quote_request','supplier_followup')"
                ),
                {"b": shop["business"]},
            )
            .mappings()
            .all()
        )
        affected = [r for r in rows if r["related_id"] != unrelated]
        assert affected and all(r["variables"]["body"] == "[erased]" for r in affected)
        assert all(
            r["recipient"] == "parts@example.com" and r["restricted_at"] is not None and r["status"] == "failed"
            for r in affected
        )
        for original in before:
            if "quote_url" in original["variables"]:
                after = next(r for r in affected if r["id"] == original["id"])
                assert after["variables"]["quote_url"] == original["variables"]["quote_url"]
        untouched = next(r for r in rows if r["related_id"] == unrelated)
        assert untouched["variables"]["body"] == "Example request" and untouched["restricted_at"] is None
        assert (
            db.scalar(
                text("select variables->>'body' from outbox_messages where business_id=:b"), {"b": other_business}
            )
            == name
        )
    with transaction(context(shop)) as db:
        exported = registry.export_handlers["notifications"](db, context(shop))
        assert all("quote_url" not in r["variables"] for r in exported["outbox_messages"])
        assert name not in str(exported)
        # A queued delivery must no longer be claimable after this transaction commits.
    with transaction(context(shop)) as db:
        handler = registry.jobs["notifications.deliver"].handler
        for row in affected:
            handler(db, context(shop), {"outbox_id": str(row["id"])})
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from delivery_attempts")) == 0
