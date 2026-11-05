"""Batch A regressions: exact money, source evidence and isolated procurement jobs."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.tests.test_procurement import extract, extract_value, rows
from app.modules.ai.tests.tool_helpers import Demo, assistant, call, seed_demo
from app.modules.ai.usecases import procurement as p
from app.modules.purchasing import public as purchasing
from app.platform import clock, jobs
from app.platform.context import system_context
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.registry import Registry, set_registry


@pytest.fixture
def demo(owner_engine: Engine, registry: Registry) -> Demo:
    set_registry(registry)
    data = seed_demo(owner_engine)
    with owner_engine.begin() as db:
        db.execute(
            text("update quote_requests set reply_by=:day where id=:i"),
            {"day": clock.now().date() - timedelta(days=1), "i": data.quote_request},
        )
        db.execute(
            text(
                "update quote_request_suppliers set status='invited',responded_at=null,last_followup_at=null "
                "where request_id=:i"
            ),
            {"i": data.quote_request},
        )
    return data


@pytest.mark.parametrize(
    ("currency", "price", "minor"),
    [("JPY", "1200", 1200), ("KWD", "1.234", 1234), ("USD", "11.40", 1140)],
)
@pytest.mark.parametrize("supplied", [True, False])
def test_currency_exponent_reaches_proposal(
    demo: Demo, owner_engine: Engine, currency: str, price: str, minor: int, supplied: bool
) -> None:
    with owner_engine.begin() as db:
        db.execute(text("update businesses set currency=:c where id=:b"), {"c": currency, "b": demo.business})
    value = extract_value(demo)
    value["currency"] = currency if supplied else None
    value["lines"][0]["unit_price"] = price
    out = extract(demo, value, f"Unit price {price} {currency}. Delivery 3 days.")
    assert out.verified and out.proposal_id is not None
    payload = rows(owner_engine, demo.business, "record_supplier_quote")[0].payload
    assert payload["currency"] == currency
    assert payload["lines"][0]["unit_price_minor"] == minor


@pytest.mark.parametrize(("currency", "price"), [("JPY", "1.2"), ("KWD", "1.2345"), ("USD", "1.234")])
def test_fractional_minor_unit_is_rejected(demo: Demo, currency: str, price: str) -> None:
    value = extract_value(demo)
    value["currency"] = currency
    value["lines"][0]["unit_price"] = price
    out = extract(demo, value, f"Unit price {currency} {price}. Delivery 3 days.")
    assert not out.verified and out.proposal_id is None
    assert "lines.0.unit_price" in out.unverified_fields


def test_rejected_currency_blocks_proposal(demo: Demo, owner_engine: Engine) -> None:
    out = extract(demo, extract_value(demo), "Unit price EUR 11.40 each. Delivery 3 days.")
    assert not out.verified and out.proposal_id is None
    assert "currency" in out.unverified_fields
    assert not rows(owner_engine, demo.business, "record_supplier_quote")


@pytest.mark.parametrize("price", ["0", "11.40"])
def test_price_evidence_does_not_use_availability(demo: Demo, price: str) -> None:
    value = extract_value(demo)
    value["lines"][0]["unit_price"] = price
    out = extract(demo, value, "Unit price USD 11.40. Availability 0. Delivery 3 days.")
    assert out.verified is (price == "11.40")
    assert out.extract.lines[0].unit_price == (price if price == "11.40" else None)


@pytest.mark.parametrize("source", ["USD. Availability 11.40. Delivery 3 days.", "USD. Stock 11.40; lead time 3 days."])
def test_number_without_price_evidence_is_rejected(demo: Demo, source: str) -> None:
    out = extract(demo, extract_value(demo), source)
    assert not out.verified and "lines.0.unit_price" in out.unverified_fields


@pytest.mark.parametrize(
    ("price", "source", "minor"),
    [
        ("11,40", "Unit price: 11,40 USD", 1140),
        ("1.234,56", "Price: USD 1.234,56", 123456),
        ("1,234.56", "$1,234.56 each", 123456),
        ("1 234,56", "USD 1 234,56 per unit", 123456),
        ("11.40", "USD; price @ 11.40", 1140),
        ("0", "USD; 0 each", 0),
    ],
)
def test_price_occurrences_keep_supplier_number_formats(
    demo: Demo, owner_engine: Engine, price: str, source: str, minor: int
) -> None:
    value = extract_value(demo)
    value["lines"][0]["unit_price"] = price
    out = extract(demo, value, source + ". Delivery 3 days.")
    assert out.verified
    assert (
        rows(owner_engine, demo.business, "record_supplier_quote")[0].payload["lines"][0]["unit_price_minor"] == minor
    )


@pytest.mark.parametrize(
    ("source", "days", "verified"),
    [
        ("Availability 3.", 3, False),
        ("Lead time 3.", 3, False),
        ("Delivery 3 days.", 3, True),
        ("Delivery 2 weeks.", 14, True),
        ("Delivery 2 weeks. Availability 2.", 2, False),
    ],
)
def test_lead_time_requires_day_or_week_expression(demo: Demo, source: str, days: int, verified: bool) -> None:
    value = extract_value(demo)
    value["lead_time_days"] = days
    out = extract(demo, value, "Unit price USD 11.40. " + source)
    assert out.verified is verified
    assert ("lead_time_days" in out.unverified_fields) is not verified


def _targets(demo: Demo) -> tuple[list[Any], list[Any]]:
    ctx, _ = assistant(demo.business)
    with transaction(ctx) as db:
        return purchasing.list_late_purchase_orders(db, ctx), purchasing.list_unanswered_invites(db, ctx)


def test_scan_fans_out_deduplicated_nontransactional_targets(
    demo: Demo, owner_engine: Engine, registry: Registry
) -> None:
    late, invites = _targets(demo)
    gateway = FakeGateway(lambda _: '{"body":"Please send an update."}')
    p.scan(demo.business, router=fake_router_by_class(writer=gateway))
    p.scan(demo.business, router=fake_router_by_class(writer=gateway))
    assert not gateway.calls
    assert not rows(owner_engine, demo.business, "send_supplier_followup")
    with owner_engine.connect() as db:
        queued = db.execute(text("select payload,dedupe_key from jobs where kind='ai.procurement_followup'")).all()
    expected = [{"purchase_order_id": str(t.id)} for t in late] + [
        {"quote_request_id": str(t.quote_request_id), "supplier_id": str(t.supplier_id)} for t in invites
    ]
    assert len(queued) == len(expected) > 0
    assert all(row.payload in expected and str(demo.business) in row.dedupe_key for row in queued)
    assert not registry.jobs["ai.procurement_followup"].transactional
    at = clock.now() + timedelta(hours=1)
    clock.set_clock(lambda: at)
    p.scan(demo.business)
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from jobs where kind='ai.procurement_followup'")) == 2 * len(expected)


def test_failing_target_job_does_not_block_other_targets(
    demo: Demo, owner_engine: Engine, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    p.scan(demo.business)
    with owner_engine.begin() as db:
        queued = (
            db.execute(
                text(
                    "update jobs set status='running',attempts=1,locked_by='batch-a' "
                    "where kind='ai.procurement_followup' returning *"
                )
            )
            .mappings()
            .all()
        )
    assert len(queued) >= 2
    original = p._followup
    gateway = FakeGateway(lambda _: '{"body":"Please send an update."}')
    seen: list[uuid.UUID] = []

    def followup(business_id: uuid.UUID, target: Any, **kwargs: Any) -> Any:
        seen.append(target.id)
        if len(seen) == 1:
            raise AiError("ai.provider_unavailable")
        return original(business_id, target, router=fake_router_by_class(writer=gateway))

    monkeypatch.setattr(p, "_followup", followup)
    outcomes = [jobs.run_job(registry, dict(job)) for job in queued]
    assert outcomes == [False, *[True] * (len(queued) - 1)]
    assert len(seen) == len(queued)
    assert rows(owner_engine, demo.business, "send_supplier_followup")


@pytest.mark.parametrize("kind", ["po", "invite"])
def test_followup_tool_uses_point_reads(demo: Demo, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    late, invites = _targets(demo)
    target = late[0] if kind == "po" else invites[0]

    def no_list(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("a per-target followup must not reload every target")

    monkeypatch.setattr(purchasing, "list_late_purchase_orders", no_list)
    monkeypatch.setattr(purchasing, "list_unanswered_invites", no_list)
    ctx, state = assistant(demo.business)
    args = (
        {"purchase_order_id": str(target.id)}
        if kind == "po"
        else {"quote_request_id": str(target.quote_request_id), "supplier_id": str(target.supplier_id)}
    )
    assert call(ctx, state, "propose_supplier_followup", {**args, "body": "Please send an update."})["ok"]


def test_public_target_reads_match_lists_and_enforce_scope(demo: Demo) -> None:
    late, invites = _targets(demo)
    ctx, _ = assistant(demo.business)
    other, _ = assistant(demo.other)
    with transaction(ctx) as db:
        for po in late:
            assert purchasing.get_late_purchase_order(db, ctx, po.id) == po
        for invite in invites:
            assert purchasing.get_unanswered_invite(db, ctx, invite.quote_request_id, invite.supplier_id) == invite
    with transaction(other) as db:
        assert purchasing.get_late_purchase_order(db, other, late[0].id) is None
        assert purchasing.get_unanswered_invite(db, other, invites[0].quote_request_id, invites[0].supplier_id) is None
    denied = system_context(demo.business)
    with transaction(denied) as db, pytest.raises(Forbidden):
        purchasing.get_late_purchase_order(db, denied, late[0].id)
    with transaction(denied) as db, pytest.raises(Forbidden):
        purchasing.get_unanswered_invite(db, denied, invites[0].quote_request_id, invites[0].supplier_id)


@pytest.mark.parametrize(
    "change", ["received", "not_late", "no_open_lines", "invite_answered", "request_closed", "expired"]
)
def test_point_reads_reject_no_longer_eligible_targets(demo: Demo, owner_engine: Engine, change: str) -> None:
    late, invites = _targets(demo)
    po, invite = late[0], invites[0]
    with owner_engine.begin() as db:
        if change == "received":
            db.execute(text("update purchase_orders set status='received' where id=:i"), {"i": po.id})
        elif change == "not_late":
            db.execute(
                text("update purchase_orders set expected_delivery_date=:d where id=:i"),
                {"i": po.id, "d": clock.now().date() + timedelta(days=1)},
            )
        elif change == "no_open_lines":
            db.execute(
                text("update purchase_order_lines set received_quantity=quantity where purchase_order_id=:i"),
                {"i": po.id},
            )
        elif change == "invite_answered":
            db.execute(text("update quote_request_suppliers set status='responded' where id=:i"), {"i": invite.id})
        elif change == "request_closed":
            db.execute(text("update quote_requests set status='closed' where id=:i"), {"i": invite.quote_request_id})
        else:
            db.execute(
                text("update quote_requests set reply_by=:d where id=:i"),
                {"i": invite.quote_request_id, "d": clock.now().date() - timedelta(days=8)},
            )
    ctx, _ = assistant(demo.business)
    with transaction(ctx) as db:
        if change in {"received", "not_late", "no_open_lines"}:
            assert purchasing.get_late_purchase_order(db, ctx, po.id) is None
        else:
            assert purchasing.get_unanswered_invite(db, ctx, invite.quote_request_id, invite.supplier_id) is None


def test_target_job_skips_removed_target_without_inference(demo: Demo, owner_engine: Engine) -> None:
    late, _ = _targets(demo)
    with owner_engine.begin() as db:
        db.execute(text("update purchase_orders set status='received' where id=:i"), {"i": late[0].id})
    gw = FakeGateway([])
    assert p.followup(demo.business, purchase_order_id=late[0].id, router=fake_router_by_class(writer=gw)) is None
    assert not gw.calls


def test_target_job_inference_has_no_open_transaction(demo: Demo) -> None:
    from sqlalchemy import event

    from app.platform.db import get_engine

    late, _ = _targets(demo)
    engine = get_engine()
    active: set[Any] = set()

    def begin(connection: Any) -> None:
        active.add(connection)

    def end(connection: Any) -> None:
        active.discard(connection)

    def respond(_: Any) -> str:
        assert not active
        return '{"body":"Please send an update."}'

    gateway = FakeGateway(respond)
    event.listen(engine, "begin", begin)
    event.listen(engine, "commit", end)
    event.listen(engine, "rollback", end)
    try:
        assert (
            p.followup(demo.business, purchase_order_id=late[0].id, router=fake_router_by_class(writer=gateway))
            is not None
        )
        assert len(gateway.calls) == 1
    finally:
        event.remove(engine, "begin", begin)
        event.remove(engine, "commit", end)
        event.remove(engine, "rollback", end)
