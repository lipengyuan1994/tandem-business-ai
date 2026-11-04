"""Shared test helpers for the ai module (no model calls; synthetic data only)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

from app.modules.ai.agent.protocol import FinalStep, ToolCallStep
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import Router
from app.modules.ai.gateway.routes import ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.models import AiEffect, AiRun, AiRunStep, AiUsageDaily, AssistantMessage, AssistantThread
from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.platform import clock
from app.platform.context import Context, assistant_context
from app.platform.db import transaction
from app.platform.testing.auth import ctx_for

AI_TABLES = ("ai_runs", "ai_run_steps", "ai_effects", "ai_usage_daily", "assistant_threads", "assistant_messages")


def system_ctx(business_id: uuid.UUID, permissions: tuple[str, ...] = ()) -> Context:
    return ctx_for(business_id, permissions, actor="system")


def insert_run(ctx: Context, **overrides: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "use_case": "intake",
        "trigger_type": "job",
        "trigger_key": f"intake:{uuid.uuid4()}",
        "prompt_name": "intake",
        "prompt_version": 1,
        "model_class": "agent",
        "status": "running",
        "started_at": clock.now(),
    }
    values.update(overrides)
    with transaction(ctx) as db:
        run = AiRun(business_id=ctx.business_id, **values)
        db.add(run)
        db.flush()
        return run.id


def fill_all_tables(ctx: Context) -> dict[str, uuid.UUID]:
    """One row in every ai table for the context's business."""
    run_id = insert_run(ctx, result={"text": "Booked a brake service"})
    with transaction(ctx) as db:
        db.add(
            AiRunStep(
                business_id=ctx.business_id, run_id=run_id, step_no=1, kind="tool_call", tool="get_customer",
                arguments_redacted={"customer_id": str(uuid.uuid4())}, result_summary="ok",
            )
        )  # fmt: skip
        effect = AiEffect(
            business_id=ctx.business_id, trigger_key="intake:x", effect_key="a" * 64, kind="proposal",
            ref_id=uuid.uuid4(), run_id=run_id,
        )  # fmt: skip
        db.add(effect)
        db.add(AiUsageDaily(business_id=ctx.business_id, day=date(2026, 11, 3), input_tokens=10, output_tokens=5))
        thread = AssistantThread(business_id=ctx.business_id, user_id=uuid.uuid4(), title="Stock questions")
        db.add(thread)
        db.flush()
        message = AssistantMessage(
            business_id=ctx.business_id, thread_id=thread.id, role="user", content="What is low on stock?"
        )
        db.add(message)
        db.flush()
        return {"run": run_id, "thread": thread.id, "message": message.id, "effect": effect.id}


# ------------------------------------------------------------------------------- scripted model runs (AI-05)
def step_tool(tool: str, **arguments: Any) -> str:
    """JSON of a ToolCallStep with reason="test" (one scripted model reply)."""
    return ToolCallStep(kind="tool_call", tool=tool, arguments=json.loads(json.dumps(arguments, default=str)),
                        reason="test").model_dump_json()  # fmt: skip


def step_final(text: str = "done", *, proposals: Iterable[uuid.UUID] = (), needs_human: bool = False) -> str:
    return FinalStep(kind="final", message_to_staff=text, proposal_ids=list(proposals),
                     needs_human=needs_human).model_dump_json()  # fmt: skip


def assistant_ctx(
    business_id: uuid.UUID, *, user_id: uuid.UUID | None = None, permissions: frozenset[str] = AI_SYSTEM_PERMISSIONS
) -> Context:
    return assistant_context(business_id, permissions, on_behalf_of_user_id=user_id, ai_run_id=uuid.uuid4())


def fake_router_by_class(**gateways: FakeGateway) -> Router:
    """e.g. fake_router_by_class(safety=gw_s, agent=gw_a). Model classes not given have no route (→ PolicyBlocked,
    status `policy_blocked` / `ai.not_configured`). Each class gets its own provider `fake_<class>` (vendor vultr)."""
    providers = {f"fake_{c}": ProviderCfg(kind="fake", vendor="vultr") for c in gateways}
    routes = {c: [RouteEntry(provider=f"fake_{c}", model=f"fake-{c}")] for c in gateways}
    return Router(RoutesFile(providers=providers, routes=routes), {f"fake_{c}": gw for c, gw in gateways.items()})  # type: ignore[arg-type]
