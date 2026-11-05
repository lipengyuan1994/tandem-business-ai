"""Batch C regressions: permission cache, unsupported quantities and bounded requests."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.modules.ai.agent.contexts import staff_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import ContextBlock
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.models import AiRun
from app.modules.ai.schemas import Briefing
from app.modules.ai.structured import complete
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.usecases import briefing
from app.modules.org.testing import make_business
from app.platform.db import transaction
from app.platform.testing.auth import ctx_for


@pytest.mark.parametrize(
    "claim",
    [
        "a dozen jobs",
        "half the stock",
        "a quarter of orders",
        "a couple of orders",
        "a discount of 5%",
        "5 percent growth",
        "five per cent growth",
    ],
)
def test_writer_rejects_unsupported_quantities(claim):
    with pytest.raises(ValueError):
        briefing.validate_numbers(claim, '{"stock": 5}')


@pytest.mark.parametrize(
    ("claim", "facts"),
    [
        ("Stock is 5.", '{"stock":5}'),
        ("A discount of 5%.", '{"discount":"5%"}'),
        ("Half the orders.", "Half the orders."),
        ("Check the queue first.", '{"count":0}'),
    ],
)
def test_writer_keeps_supported_output(claim, facts):
    briefing.validate_numbers(claim, facts)


def test_briefing_cache_separates_effective_permissions(registry):
    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use", "org.business.read", "inventory.stock.read"})
    gateway = FakeGateway([json.dumps({"bullets": ["Stock visible."]}), json.dumps({"bullets": ["Access changed."]})])
    router = fake_router_by_class(writer=gateway)
    assert briefing.run(ctx, router=router).bullets == ["Stock visible."]
    reduced = replace(ctx, permissions=frozenset({"ai.assistant.use", "org.business.read"}))
    assert briefing.run(reduced, router=router).bullets == ["Access changed."]
    assert briefing.run(ctx, router=router).bullets == ["Stock visible."]
    assert len(gateway.calls) == 2


@pytest.mark.parametrize("repair", [False, True])
def test_structured_never_calls_above_input_limit(registry, repair):
    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use"})
    gateway = FakeGateway(["x" * 20000] if repair else [])
    router = fake_router_by_class(writer=gateway)
    router.routes.limits.max_input_tokens_per_step = 3000
    with pytest.raises(Exception) as error:
        complete(
            staff_ctx_factory(business, ctx.user_id, ctx.permissions, "briefing"),
            use_case="briefing",
            prompt=load_prompt("briefing"),
            facts=[ContextBlock(label="source", text="ok" if repair else "x" * 20000, untrusted=True)],
            output_model=Briefing,
            trigger_type="api",
            trigger_key="size-test",
            router=router,
        )
    assert error.value.code == "ai.input_too_large"
    assert len(gateway.calls) == int(repair)
    with transaction(ctx) as db:
        run = db.scalar(select(AiRun))
        assert run.status == "failed" and run.error_code == "ai.input_too_large"


@pytest.mark.parametrize("repair", [False, True])
def test_copilot_enforces_initial_and_repair_context_limit(registry, repair):
    import threading

    from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
    from app.modules.ai.usecases import copilot

    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use"})
    thread = copilot.create_thread(ctx, copilot.ThreadCreate()).id
    prepared = copilot.prepare_message(ctx, thread, copilot.MessageCreate(content="Hello."))
    if not repair:
        prepared = replace(
            prepared,
            spec=prepared.spec.model_copy(
                update={"context_blocks": [ContextBlock(label="history", text="x" * 20000, untrusted=True)]}
            ),
        )
    gateway = FakeGateway(["x" * 20000] if repair else [])
    router = fake_router_by_class(agent=gateway)
    router.routes.limits.max_input_tokens_per_step = 3000
    set_router_for_tests(router)
    events = []
    try:
        copilot.run_message(prepared, events.append, threading.Event())
        assert len(gateway.calls) == int(repair)
        assert events[-1].kind == "error" and events[-1].error_code == "ai.input_too_large"
    finally:
        reset_router_cache()
