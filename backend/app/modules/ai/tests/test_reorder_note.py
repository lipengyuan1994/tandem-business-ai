"""U4: inventory supplies quantities; the writer supplies only validated explanation text."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, event, text

from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.schemas import ReorderNote
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.tests.tool_helpers import Demo, assistant, call, ok, seed_demo
from app.modules.ai.usecases import reorder_note
from app.modules.approvals import public as approvals
from app.modules.inventory import public as inventory
from app.modules.org.testing import make_business
from app.platform import clock, events, jobs
from app.platform.context import Context, system_context
from app.platform.db import get_engine, transaction
from app.platform.registry import Registry, set_registry

NOTE = "Available stock is below the reorder point; reorder 5 to restore the target level."


@pytest.fixture(autouse=True)
def clean_router() -> Iterator[None]:
    reset_router_cache()
    yield
    reset_router_cache()


@pytest.fixture
def demo(owner_engine: Engine, registry: Registry) -> Demo:
    data = seed_demo(owner_engine)
    set_registry(registry)
    return data


def proposals(owner_engine: Engine, business: uuid.UUID) -> list[Any]:
    with owner_engine.connect() as db:
        return list(
            db.execute(
                text(
                    "select p.id, p.origin_kind, p.ai_run_id, r.payload, r.evidence from action_proposals p "
                    "join proposal_revisions r on r.id=p.current_revision_id "
                    "where p.business_id=:b and p.action_type='inventory.create_reorder_draft'"
                ),
                {"b": business},
            ).all()
        )


def test_reorder_note_quantity_from_inventory_not_model(demo: Demo, owner_engine: Engine) -> None:
    item = demo.items["Cable set"]
    ctx, _ = assistant(demo.business)
    with transaction(ctx) as db:
        suggestion = next(s for s in inventory.list_below_reorder(db, ctx) if s.item_id == item)
    gw = FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])
    result = reorder_note.run(item, demo.business, router=fake_router_by_class(writer=gw))
    rows = proposals(owner_engine, demo.business)
    assert len(rows) == 1 and rows[0].id == result
    row = rows[0]
    assert row.origin_kind == "assistant"
    assert row.payload["lines"] == [
        {"item_id": str(item), "quantity": str(suggestion.suggested.normalize()), "reason_text": NOTE}
    ]
    assert Decimal(row.payload["lines"][0]["quantity"]) == suggestion.suggested == 5
    assert any(e.get("ref_id") == str(item) for e in row.evidence)
    with owner_engine.connect() as db:
        run = db.execute(
            text("select result, prompt_name, model_class, status from ai_runs where id=:r"), {"r": row.ai_run_id}
        ).one()
        assert run == ({"explanation": NOTE}, "reorder_note", "writer", "succeeded")
        assert (
            db.execute(
                text("select count(*) from ai_effects where run_id=:r and ref_id=:p"), {"r": row.ai_run_id, "p": row.id}
            ).scalar_one()
            == 1
        )
    assert len(gw.calls) == 1
    request = gw.calls[0]
    assert request.use_case == "reorder_note"
    assert request.json_schema is not None and set(request.json_schema["properties"]) == {"explanation"}
    assert '<untrusted source="reorder_suggestion"' in request.messages[1].content
    assert "Cable set" in request.messages[1].content


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_reorder_note_rejects_invented_numbers(demo: Demo, owner_engine: Engine, repair_succeeds: bool) -> None:
    bad = ReorderNote(explanation="Order 987654321 units.").model_dump_json()
    good = ReorderNote(explanation=NOTE).model_dump_json()
    gw = FakeGateway([bad, good if repair_succeeds else bad])
    router = fake_router_by_class(writer=gw)
    item = demo.items["Cable set"]
    if repair_succeeds:
        assert reorder_note.run(item, demo.business, router=router) is not None
    else:
        with pytest.raises(AiError) as err:
            reorder_note.run(item, demo.business, router=router)
        assert err.value.code == "ai.invalid_model_output"
    assert len(gw.calls) == 2
    assert len(proposals(owner_engine, demo.business)) == int(repair_succeeds)
    with owner_engine.connect() as db:
        run = db.execute(
            text("select status, result, steps from ai_runs where use_case='reorder_note' and business_id=:b"),
            {"b": demo.business},
        ).one()
    assert run.status == ("succeeded" if repair_succeeds else "failed") and run.steps == 2
    assert "987654321" not in json.dumps(run.result)


def test_reorder_note_skips_when_pending_proposal_exists(
    demo: Demo, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = demo.items["Cable set"]
    ctx, state = assistant(demo.business)
    pending = ok(call(ctx, state, "propose_reorder", {"item_ids": [str(item)]}))["proposal_id"]
    original = approvals.find_open_proposals
    calls: list[dict[str, Any]] = []

    def find(db: Any, acting: Context, **kw: Any) -> list[approvals.OpenProposal]:
        calls.append(kw)
        return original(db, acting, **kw)

    monkeypatch.setattr(approvals, "find_open_proposals", find)
    gw = FakeGateway([])
    assert reorder_note.run(item, demo.business, router=fake_router_by_class(writer=gw)) is None
    assert calls == [{"action_types": ["inventory.create_reorder_draft"], "item_id": item, "include_executing": True}]
    assert not gw.calls and [str(p.id) for p in proposals(owner_engine, demo.business)] == [pending]


def test_reorder_note_other_item_proposal_does_not_block(demo: Demo, owner_engine: Engine) -> None:
    ctx, _ = assistant(demo.business)
    with transaction(ctx) as db:
        approvals.create_proposal(
            db, ctx, action_type="inventory.create_reorder_draft", title="Reorder brake pads",
            payload={"lines": [{"item_id": str(demo.items["Brake pads (pair)"]), "quantity": "1"}]},
        )  # fmt: skip
    gw = FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])
    assert reorder_note.run(demo.items["Cable set"], demo.business, router=fake_router_by_class(writer=gw))
    assert len(proposals(owner_engine, demo.business)) == 2 and len(gw.calls) == 1


def test_reorder_note_no_longer_below_reorder_does_nothing(demo: Demo, owner_engine: Engine) -> None:
    gw = FakeGateway([])
    assert (
        reorder_note.run(demo.items["Brake pads (pair)"], demo.business, router=fake_router_by_class(writer=gw)) is None
    )
    assert not gw.calls and not proposals(owner_engine, demo.business)


def trigger(registry: Registry, business: uuid.UUID, item: uuid.UUID) -> None:
    ctx = system_context(business)
    with transaction(ctx) as db:
        registry.subscribers["inventory.stock_below_reorder_level"]["ai.reorder-trigger"](
            db, ctx, {"item_id": str(item)}, uuid.uuid4()
        )


def test_reorder_subscriber_dedupes_item_and_utc_day(registry: Registry, owner_engine: Engine) -> None:
    business = make_business()
    item, other = uuid.uuid4(), uuid.uuid4()
    gw = FakeGateway([])
    set_router_for_tests(fake_router_by_class(writer=gw))
    at = datetime(2026, 11, 5, 23, 59, tzinfo=UTC)
    clock.set_clock(lambda: at)
    trigger(registry, business, item)
    trigger(registry, business, item)
    trigger(registry, business, other)
    at += timedelta(minutes=2)
    trigger(registry, business, item)
    with owner_engine.connect() as db:
        rows = db.execute(text("select kind, payload, business_id, dedupe_key from jobs")).all()
    assert len(rows) == 3
    assert {r.dedupe_key for r in rows} == {
        f"reorder_note:{item}:2026-11-05",
        f"reorder_note:{other}:2026-11-05",
        f"reorder_note:{item}:2026-11-06",
    }
    assert all(r.kind == "ai.reorder_note" and r.business_id == business for r in rows)
    assert [r.payload for r in rows].count({"item_id": str(item)}) == 2
    assert not gw.calls


def test_reorder_job_registration_runs_without_worker_transaction(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    business, item = make_business(), uuid.uuid4()
    active: set[Any] = set()
    seen = []
    engine = get_engine()

    def begin(conn: Any) -> None:
        active.add(conn)

    def end(conn: Any) -> None:
        active.discard(conn)

    def run(item_id: uuid.UUID, business_id: uuid.UUID) -> None:
        assert not active
        seen.append((item_id, business_id))

    monkeypatch.setattr(reorder_note, "run", run)
    trigger(registry, business, item)
    assert registry.jobs["ai.reorder_note"].transactional is False
    event.listen(engine, "begin", begin)
    event.listen(engine, "commit", end)
    event.listen(engine, "rollback", end)
    try:
        claimed = jobs.claim("reorder-test")
        assert claimed is not None and jobs.run_job(registry, claimed)
    finally:
        event.remove(engine, "begin", begin)
        event.remove(engine, "commit", end)
        event.remove(engine, "rollback", end)
    assert seen == [(item, business)]


def test_reorder_job_completes_with_real_usecase(demo: Demo, registry: Registry, owner_engine: Engine) -> None:
    gw = FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])
    set_router_for_tests(fake_router_by_class(writer=gw))
    with owner_engine.begin() as db:
        # The seed contains unrelated queued jobs; leave only this test's U4 enqueue claimable.
        db.execute(text("update jobs set run_at=:later"), {"later": clock.now() + timedelta(days=1)})
    trigger(registry, demo.business, demo.items["Cable set"])
    claimed = jobs.claim("reorder-test")
    assert claimed is not None and claimed["kind"] == "ai.reorder_note"
    assert jobs.run_job(registry, claimed)
    assert len(proposals(owner_engine, demo.business)) == 1 and len(gw.calls) == 1


def autonomy_rule(demo: Demo, owner_engine: Engine, *, level: str = "act_notify", live: bool = False) -> None:
    """Adjust the seeded shadow rule through the public API as its existing owner."""
    from app.modules.approvals import public as approvals_api
    from app.modules.identity import public as identity

    with owner_engine.connect() as db:
        grantor, version = db.execute(
            text(
                "select granted_by,version from autonomy_rules where business_id=:b "
                "and action_type='inventory.create_reorder_draft'"
            ),
            {"b": demo.business},
        ).one()
    ctx = Context("test", "user", grantor, demo.business, identity.ROLES["owner"])
    with transaction(ctx) as db:
        approvals_api.put_autonomy_rule(
            db,
            ctx,
            approvals_api.RulePut(
                action_type="inventory.create_reorder_draft",
                level=level,
                shadow=not live,
                version=version,
            ),
        )


def test_off_rule_skips_writer_without_retry(demo: Demo, owner_engine: Engine) -> None:
    autonomy_rule(demo, owner_engine, level="off")
    gw = FakeGateway([])
    assert reorder_note.run(demo.items["Cable set"], demo.business, router=fake_router_by_class(writer=gw)) is None
    assert gw.calls == [] and proposals(owner_engine, demo.business) == []


def test_off_rule_changed_during_generation_is_handled(
    demo: Demo, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = reorder_note.complete

    def complete_then_disable(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        autonomy_rule(demo, owner_engine, level="off")
        return result

    monkeypatch.setattr(reorder_note, "complete", complete_then_disable)
    gw = FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])
    assert reorder_note.run(demo.items["Cable set"], demo.business, router=fake_router_by_class(writer=gw)) is None
    assert len(gw.calls) == 1 and proposals(owner_engine, demo.business) == []


def test_low_stock_event_policy_execution_and_next_day_intent(
    demo: Demo, owner_engine: Engine, registry: Registry
) -> None:

    autonomy_rule(demo, owner_engine, live=True)
    gw = FakeGateway([ReorderNote(explanation=NOTE).model_dump_json()])
    set_router_for_tests(fake_router_by_class(writer=gw))
    item = demo.items["Cable set"]
    ctx = system_context(demo.business)
    # Cross the actual low-stock threshold; the inventory event is delivered by the real event worker.
    with transaction(ctx) as db:
        inventory.record_movement(db, ctx, inventory.MovementIn(item_id=item, quantity=Decimal(5), reason="receipt"))
        inventory.record_movement(
            db, ctx, inventory.MovementIn(item_id=item, quantity=Decimal(-5), reason="consumption")
        )
    events.deliver_pending(registry)
    trigger(registry, demo.business, item)  # duplicate delivery retains one job
    with owner_engine.connect() as db:
        rows = db.execute(
            text("select payload from jobs where kind='ai.reorder_note' and business_id=:b"), {"b": demo.business}
        ).all()
    assert len(rows) == 1
    handler = registry.jobs["ai.reorder_note"]
    assert not handler.transactional
    handler.handler(system_context(demo.business), rows[0].payload)
    with owner_engine.connect() as db:
        p = db.execute(
            text(
                "select id,current_revision_id,status from action_proposals "
                "where action_type='inventory.create_reorder_draft'"
            )
        ).one()
    assert p.status == "approved"
    execute_job = jobs.claim("a0-execute")
    assert execute_job is not None and execute_job["kind"] == "approvals.execute"
    assert jobs.run_job(registry, execute_job)
    with transaction(ctx) as db:
        page = approvals.autonomy_activity(db, system_context(demo.business, frozenset({"approvals.proposal.read"})))
        assert page.items[0].status == "succeeded" and page.items[0].reorder_draft_id is not None
    clock.set_clock(lambda: datetime(2026, 11, 6, 17, 30, tzinfo=UTC))
    trigger(registry, demo.business, item)
    assert reorder_note.run(item, demo.business, router=fake_router_by_class(writer=gw)) is None
    with owner_engine.connect() as db:
        assert db.execute(text("select count(*) from reorder_drafts")).scalar_one() == 1
        assert db.execute(text("select outcome from autonomy_evaluations")).scalar_one() == "acted"
    assert len(proposals(owner_engine, demo.business)) == 1 and len(gw.calls) == 1
