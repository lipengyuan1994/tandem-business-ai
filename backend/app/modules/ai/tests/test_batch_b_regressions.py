"""Second review batch B: real transactions, synthetic inputs and FakeGateway only."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import Engine, select, text

from app.modules.ai.agent import runner
from app.modules.ai.agent.contexts import background_ctx_factory, staff_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import RunSpec
from app.modules.ai.gateway import router as router_module
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import Router
from app.modules.ai.gateway.routes import Limits, ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.gateway.types import ChatRequest, ProviderError
from app.modules.ai.models import AiRun
from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool, system_ctx
from app.modules.ai.tests.test_intake import inbound, notes
from app.modules.ai.tests.tool_helpers import Demo, assistant, call, ok, seed_demo
from app.modules.ai.tools import proposals, scheduling
from app.modules.ai.usecases import intake
from app.modules.conversations import public as conversations
from app.modules.org.testing import make_business
from app.modules.scheduling import public as scheduling_public
from app.platform import clock
from app.platform.db import transaction
from app.platform.errors import Conflict


@pytest.fixture
def demo(owner_engine: Engine) -> Demo:
    return seed_demo(owner_engine)


def spec(**kw: Any) -> RunSpec:
    return RunSpec(
        use_case="copilot",
        prompt=load_prompt("copilot"),
        tools=kw.pop("tools", []),
        task="Help staff.",
        context_blocks=[],
        trigger_type="api",
        trigger_key=f"test:{uuid.uuid4()}",
        **kw,
    )


@pytest.mark.parametrize(
    "tool,required",
    [
        ("propose_reorder", "inventory.stock.read"),
        ("propose_booking", "scheduling.appointment.read"),
        ("propose_booking", "catalog.item.read"),
        ("propose_booking", "crm.customer.read"),
        ("propose_reply", "conversations.thread.read"),
        ("propose_payment_reminder", "sales.document.read"),
        ("propose_quote_request", "purchasing.po.read"),
        ("propose_quote_request", "inventory.stock.read"),
        ("propose_supplier_quote", "purchasing.po.read"),
        ("propose_po_from_quote", "purchasing.po.read"),
        ("propose_supplier_followup", "purchasing.po.read"),
    ],
)
def test_proposals_require_lookup_permission_before_validation(tool: str, required: str) -> None:
    ctx, state = assistant(make_business(), permissions=AI_SYSTEM_PERMISSIONS - {required})
    observed = call(ctx, state, tool, {})
    assert observed == {"ok": False, "error": "auth.permission_denied", "detail": required}


def test_copilot_catalog_excludes_proposals_without_read_permission() -> None:
    business = make_business()
    gw = FakeGateway([step_final()])
    factory = staff_ctx_factory(business, uuid.uuid4(), frozenset({"ai.assistant.use"}), "copilot")
    result = runner.run_agent(
        factory,
        spec(tools=["propose_reorder", "propose_booking", "propose_reply", "propose_customer"]),
        router=fake_router_by_class(agent=gw),
    )
    assert result.status == "succeeded"
    catalog = gw.calls[0].messages[0].content
    assert '"propose_customer"' in catalog
    assert all(f'"{tool}"' not in catalog for tool in ("propose_reorder", "propose_booking", "propose_reply"))


def booking_args(demo: Demo) -> dict[str, Any]:
    return {
        "service_id": str(demo.items["Brake service"]),
        "resource_id": str(demo.resources["Jordan (technician)"]),
        "starts_at": "2026-11-10T18:45:00Z",
    }


def test_intake_reply_then_booking_refused(demo: Demo) -> None:
    conv, message = inbound(demo.business, channel="email")
    ctx, state = assistant(demo.business, trigger_key=f"intake:{message}", trigger_message_id=message)
    state.server_facts["visitor_name"] = "Rowan Example"
    state.read_conversation_ids.add(conv)
    ok(call(ctx, state, "propose_reply", {"conversation_id": str(conv), "text": "Which time works?"}))
    refused = call(ctx, state, "propose_booking", booking_args(demo))
    assert refused["ok"] is False and refused["error"] == "ai.intake_outcome_exists"


def test_intake_competing_proposals_commit_only_one(
    demo: Demo, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv, message = inbound(demo.business, channel="email")
    pairs = [assistant(demo.business, trigger_key=f"intake:{message}", trigger_message_id=message) for _ in range(2)]
    for _, state in pairs:
        state.server_facts["visitor_name"] = "Rowan Example"
        state.read_conversation_ids.add(conv)
    # Both transactions must get past their ordinary prechecks before either commits.
    barrier = Barrier(2)
    original = proposals.propose

    def simultaneous(*args: Any, **kwargs: Any) -> Any:
        barrier.wait(timeout=15)
        return original(*args, **kwargs)

    monkeypatch.setattr(proposals, "propose", simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(call, *pairs[0], "propose_reply", {"conversation_id": str(conv), "text": "When?"}),
            pool.submit(call, *pairs[1], "propose_booking", booking_args(demo)),
        ]
        results = [job.result(timeout=30) for job in jobs]
    assert sum(bool(result["ok"]) for result in results) == 1, results
    assert next(r for r in results if not r["ok"])["error"] == "ai.intake_outcome_exists"
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from action_proposals where conversation_id=:c"), {"c": conv}) == 1


def test_router_fallback_uses_remaining_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(router_module, "_monotonic", lambda: now[0], raising=False)

    def transient(req: ChatRequest) -> str:
        now[0] += 3
        raise ProviderError("server_error")

    first, second = FakeGateway(transient), FakeGateway([step_final()])
    router = Router(
        RoutesFile(
            providers={"a": ProviderCfg(kind="fake", vendor="vultr"), "b": ProviderCfg(kind="fake", vendor="vultr")},
            routes={"agent": [RouteEntry(provider="a", model="a"), RouteEntry(provider="b", model="b")]},
        ),
        {"a": first, "b": second},
    )
    router.complete("agent", "hosted_any", ChatRequest(messages=[], timeout_s=5))
    assert [first.calls[0].timeout_s, second.calls[0].timeout_s] == [5, 2]


@pytest.mark.parametrize("tool", [False, True])
def test_late_completion_cannot_finish_or_execute_tool(monkeypatch: pytest.MonkeyPatch, tool: bool) -> None:
    business = make_business()
    now = [100.0]
    monkeypatch.setattr(runner, "_monotonic", lambda: now[0])
    executed = []
    monkeypatch.setattr(runner, "execute_tool", lambda *a, **kw: executed.append(a) or {"ok": True})

    def late(req: ChatRequest) -> str:
        now[0] += 6
        return step_tool("list_services", query="") if tool else step_final()

    router = fake_router_by_class(agent=FakeGateway(late))
    router.limits = Limits(run_timeout_s=5)
    result = runner.run_agent(background_ctx_factory(business), spec(tools=["list_services"]), router=router)
    assert (result.status, result.error_code) == ("failed", "ai.run_timeout")
    assert executed == []


def hold_args(demo: Demo) -> dict[str, Any]:
    return {**booking_args(demo), "customer_id": str(demo.customers["Ada Example"])}


@pytest.mark.parametrize("released", [False, True])
def test_expired_hold_replay_reports_expiry_then_allows_reacquisition(
    demo: Demo, owner_engine: Engine, released: bool
) -> None:
    ctx, state = assistant(demo.business)
    args = hold_args(demo)
    first = ok(call(ctx, state, "hold_slot", args))["hold_id"]
    if released:
        with owner_engine.begin() as db:
            db.execute(text("update appointments set status='cancelled' where id=:i"), {"i": first})
    else:
        clock.set_clock(lambda: datetime(2026, 11, 5, 18, 1, tzinfo=UTC))
    replay = call(ctx, state, "hold_slot", args)
    assert replay["ok"] is False and replay["error"] == "ai.hold_expired"
    second = ok(call(ctx, state, "hold_slot", args))["hold_id"]
    assert second != first
    assert ok(call(ctx, state, "hold_slot", args))["hold_id"] == second


def test_booking_refuses_unreaped_expired_hold(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    hold = ok(call(ctx, state, "hold_slot", hold_args(demo)))
    clock.set_clock(lambda: datetime(2026, 11, 5, 18, 1, tzinfo=UTC))
    result = call(ctx, state, "propose_booking", {"hold_id": hold["hold_id"]})
    assert result["ok"] is False and result["error"] == "ai.hold_expired"


def test_public_binding_refuses_expired_hold(demo: Demo) -> None:
    ctx, state = assistant(demo.business)
    hold = ok(call(ctx, state, "hold_slot", hold_args(demo)))
    clock.set_clock(lambda: datetime(2026, 11, 5, 18, 1, tzinfo=UTC))
    state.read_conversation_ids.add(demo.conversations["Ada"])
    proposal = ok(
        call(
            ctx,
            state,
            "propose_reply",
            {"conversation_id": str(demo.conversations["Ada"]), "text": "Please check the time."},
        )
    )
    with transaction(ctx) as db, pytest.raises(Conflict, match=r"scheduling\.hold_not_bindable"):
        scheduling_public.bind_hold(db, ctx, uuid.UUID(hold["hold_id"]), uuid.UUID(proposal["proposal_id"]))


@pytest.mark.parametrize("stage", ["state", "policy", "provider", "tool"])
def test_unexpected_exception_closes_run(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    business = make_business()

    def broken(*a: Any, **kw: Any) -> Any:
        raise OverflowError("private input must not be logged")

    if stage == "state":
        monkeypatch.setattr(runner, "_rebuild_state", broken)
    elif stage == "policy":
        monkeypatch.setattr(runner, "effective_policy", broken)
    elif stage == "tool":
        monkeypatch.setattr(runner, "execute_tool", broken)
    gw = FakeGateway(broken if stage == "provider" else [step_tool("list_services", query="")])
    with pytest.raises(OverflowError):
        runner.run_agent(
            background_ctx_factory(business), spec(tools=["list_services"]), router=fake_router_by_class(agent=gw)
        )
    with transaction(system_ctx(business)) as db:
        row = db.scalar(select(AiRun))
        assert row is not None and (row.status, row.error_code) == ("failed", "ai.internal_error")
        assert row.finished_at is not None


@pytest.mark.parametrize(
    "tool,fields", [("find_slots", ("earliest", "latest")), ("list_appointments", ("start", "end"))]
)
@pytest.mark.parametrize("year", [1, 9999])
def test_tool_dates_bounded_before_scheduling_lookup(
    demo: Demo, monkeypatch: pytest.MonkeyPatch, tool: str, fields: tuple[str, str], year: int
) -> None:
    def forbidden(*a: Any, **kw: Any) -> Any:
        pytest.fail("out-of-range dates reached scheduling")

    monkeypatch.setattr(scheduling.scheduling, tool, forbidden)
    ctx, state = assistant(demo.business)
    args = {fields[0]: f"{year:04d}-12-28T00:00:00Z", fields[1]: f"{year:04d}-12-31T00:00:00Z"}
    if tool == "find_slots":
        args["service_id"] = str(demo.items["Brake service"])
    assert call(ctx, state, tool, args)["error"] == "platform.validation_error"


def test_outcome_marker_uses_public_point_query(demo: Demo, monkeypatch: pytest.MonkeyPatch) -> None:
    conv, message = inbound(demo.business, channel="email")
    result = intake.run(message, demo.business, router=fake_router_by_class(agent=FakeGateway([step_final()])))

    def forbidden(*a: Any, **kw: Any) -> Any:
        pytest.fail("marker check loaded thread history")

    with monkeypatch.context() as patch:
        patch.setattr(conversations, "get_thread", forbidden)
        intake._outcome_note(background_ctx_factory(demo.business), conv, message, result)
    assert len(notes(demo.business, conv)) == 1


def test_intake_context_supports_five_step_booking(demo: Demo, owner_engine: Engine) -> None:
    conv, message = inbound(demo.business, channel="email", body="Brake service Tuesday morning please.")
    script = [
        step_tool("check_stock", item_ids=[demo.items["Brake pads (pair)"]]),
        step_tool(
            "find_slots",
            service_id=demo.items["Brake service"],
            earliest="2026-11-10T18:45:00Z",
            latest="2026-11-10T19:45:00Z",
        ),
        step_tool("hold_slot", **hold_args(demo)),
    ]
    replies = iter(script)

    def model(req: ChatRequest) -> str:
        if len(gw.calls) <= 3:
            return next(replies)
        if len(gw.calls) == 4:
            observation = json.loads(req.messages[-1].content.split("\n", 2)[2].rsplit("\n", 1)[0])
            return step_tool(
                "propose_booking",
                hold_id=observation["data"]["hold_id"],
                reply={"conversation_id": str(conv), "body": "Tuesday 11:45, pending approval."},
            )
        return step_final("Prepared for approval.")

    gw = FakeGateway(model)
    result = intake.run(message, demo.business, router=fake_router_by_class(agent=gw))
    assert result.status == "succeeded" and len(result.proposal_ids) == 1 and len(gw.calls) == 5
    context = gw.calls[0].messages[1].content.split('<untrusted source="inbound_message"')[0]
    assert all(
        value in context
        for value in [
            "Ada Example",
            "Brake service",
            "duration_minutes",
            "price",
            "parts",
            str(demo.items["Brake pads (pair)"]),
            "America/Denver",
        ]
    )
    assert "ada@example.com" not in context and "+12025550123" not in context
    assert load_prompt("intake").version >= 2  # v2 introduced the preloaded context; later versions keep it


def test_intake_claim_rolls_back_with_invalid_proposal_and_replays_success(demo: Demo) -> None:
    conv, message = inbound(demo.business, channel="email")
    ctx, state = assistant(demo.business, trigger_key=f"intake:{message}", trigger_message_id=message)
    state.read_conversation_ids.add(conv)
    state.server_facts["visitor_name"] = "Rowan Example"
    invalid = call(ctx, state, "propose_booking", {**booking_args(demo), "starts_at": "2026-11-10T17:00:00Z"})
    assert invalid["ok"] is False
    args = {"conversation_id": str(conv), "text": "What time suits you?"}
    first = ok(call(ctx, state, "propose_reply", args))
    retry_ctx, retry_state = assistant(demo.business, trigger_key=f"intake:{message}", trigger_message_id=message)
    assert ok(call(retry_ctx, retry_state, "propose_reply", args))["proposal_id"] == first["proposal_id"]
    assert call(retry_ctx, retry_state, "propose_booking", booking_args(demo))["error"] == "ai.intake_outcome_exists"


def test_public_context_reads_are_scoped_and_permission_checked(demo: Demo, owner_engine: Engine) -> None:
    from app.modules.catalog import public as catalog
    from app.platform.context import system_context
    from app.platform.errors import Forbidden, NotFound

    ctx, _ = assistant(demo.business)
    conv = demo.conversations["Ada"]
    marker = "[intake:example_%]"
    with transaction(ctx) as db:
        conversations.add_internal_note(db, ctx, conv, "staff " + marker, author_kind="system")
        assert not conversations.has_internal_note_marker(db, ctx, conv, marker)
        conversations.add_internal_note(db, ctx, conv, "assistant " + marker, author_kind="assistant")
        assert conversations.has_internal_note_marker(db, ctx, conv, marker)
        assert not conversations.has_internal_note_marker(db, ctx, conv, "[intake:example__%]")
        with pytest.raises(NotFound):
            conversations.has_internal_note_marker(db, ctx, demo.other_conversations["Hal Example"], marker)
        services = catalog.list_active_service_requirements(db, ctx)
        assert services and {s.service_id for s in services} <= set(demo.items.values())
        assert all(s.duration_minutes > 0 and s.currency == "USD" for s in services)
        assert next(s for s in services if s.name == "Brake service").parts
    denied = system_context(demo.business, frozenset())
    with transaction(denied) as db:
        with pytest.raises(Forbidden):
            conversations.has_internal_note_marker(db, denied, conv, marker)
        with pytest.raises(Forbidden):
            catalog.list_active_service_requirements(db, denied)
    with owner_engine.begin() as db:
        db.execute(text("update catalog_items set status='archived' where id=:i"), {"i": demo.items["Brake service"]})
    with transaction(ctx) as db:
        assert demo.items["Brake service"] not in {
            s.service_id for s in catalog.list_active_service_requirements(db, ctx)
        }


def test_router_rejects_late_provider_result(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr(router_module, "_monotonic", lambda: now[0])

    def late(req: ChatRequest) -> str:
        now[0] += 6
        return step_final()

    router = fake_router_by_class(agent=FakeGateway(late))
    with pytest.raises(ProviderError) as error:
        router.complete("agent", "hosted_any", ChatRequest(messages=[], timeout_s=5))
    assert error.value.kind == "timeout"


def test_legacy_intake_proposal_also_blocks_another_outcome(demo: Demo) -> None:
    from app.modules.ai import effects
    from app.modules.approvals import public as approvals

    conv, message = inbound(demo.business, channel="email")
    ctx, state = assistant(demo.business, trigger_key=f"intake:{message}", trigger_message_id=message)
    state.server_facts["visitor_name"] = "Rowan Example"
    args = proposals.ProposeReplyArgs(conversation_id=conv, text="Which time suits you?")
    # Simulate the old deployment: one ordinary proposal effect, no shared outcome claim.
    with transaction(ctx) as db:
        proposal = approvals.create_proposal(
            db,
            ctx,
            action_type="conversations.send_reply",
            payload={"conversation_id": str(conv), "body": args.text},
            title="Reply to visitor",
            conversation_id=conv,
        )
        effects.record_effect(
            db,
            ctx,
            run_id=state.run_id,
            trigger_key=state.trigger_key,
            effect_key=effects.effect_key("propose_reply", args, trigger_key=state.trigger_key),
            kind="proposal",
            ref_id=proposal.id,
        )
    assert call(ctx, state, "propose_booking", booking_args(demo))["error"] == "ai.intake_outcome_exists"
    assert ok(call(ctx, state, "propose_reply", args.model_dump(mode="json")))["proposal_id"] == str(proposal.id)


def test_linked_reply_checks_crm_permission_before_customer_lookup(demo: Demo, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.crm import public as crm

    conv, _ = inbound(demo.business, channel="email", customer_id=demo.customers["Ada Example"])
    ctx, state = assistant(demo.business, permissions=AI_SYSTEM_PERMISSIONS - {"crm.customer.read"})
    state.read_conversation_ids.add(conv)

    def forbidden(*a: Any, **kw: Any) -> Any:
        pytest.fail("customer data was read without permission")

    monkeypatch.setattr(crm, "customer_names", forbidden)
    assert call(ctx, state, "propose_reply", {"conversation_id": str(conv), "text": "Which time?"}) == {
        "ok": False,
        "error": "auth.permission_denied",
        "detail": "crm.customer.read",
    }
    # A visitor-only reply has no CRM lookup and needs only the conversation read permission.
    visitor_conv, _ = inbound(demo.business, channel="email")
    state.read_conversation_ids.add(visitor_conv)
    ok(call(ctx, state, "propose_reply", {"conversation_id": str(visitor_conv), "text": "Which time?"}))
