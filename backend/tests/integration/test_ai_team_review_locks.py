"""Force rejected admission to race the real reservation lock path, without relying on scheduling luck."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC
from threading import Event, local
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.modules.ai import recording
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.routes import RouteEntry
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.models import AiRun, AiUsageDaily
from app.modules.ai.reservations import reserve
from app.modules.ai.team_models import ChainBudget
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.context import system_context
from app.platform.db import get_engine, transaction


def test_rejected_admission_and_reservation_have_compatible_lock_order(registry):
    business = make_business("review-lock-order")
    root = uuid4()
    ctx = replace(system_context(business), agent_key="inventory", correlation_id=root, chain_depth=1)
    rejected = replace(ctx, ai_run_id=uuid4(), causing_agent_key="purchasing", chain_depth=2)
    with transaction(ctx) as db:
        db.add(AiUsageDaily(business_id=business, day=clock.now().astimezone(UTC).date()))
        db.add(ChainBudget(business_id=business, correlation_id=root))
    daily_locked, admission_at_daily = Event(), Event()
    role = local()

    def after_sql(conn, cursor, statement, parameters, context, executemany):
        if getattr(role, "name", None) == "reserve" and statement.lower().startswith("update ai_usage_daily"):
            daily_locked.set()
            assert admission_at_daily.wait(5), "rejected admission never reached business usage"

    def before_sql(conn, cursor, statement, parameters, context, executemany):
        if getattr(role, "name", None) == "admission" and statement.lower().startswith("insert into ai_usage_daily"):
            admission_at_daily.set()

    def reservation():
        role.name = "reserve"
        try:
            reserve(
                ctx,
                RouteEntry(provider="fake", model="fake"),
                ChatRequest(messages=[], max_output_tokens=1, use_case="reorder_note"),
            )
            return "reserved"
        except SQLAlchemyError as exc:
            return type(exc).__name__

    def admission():
        role.name = "admission"
        try:
            recording.open_run(
                rejected,
                recording.RunMeta(
                    use_case="reorder_note",
                    prompt=load_prompt("reorder_note"),
                    trigger_type="job",
                    trigger_key="review-rejected-admission",
                ),
            )
        except AiError as exc:
            return exc.code
        except SQLAlchemyError as exc:
            return type(exc).__name__
        return "incorrectly admitted"

    engine = get_engine()
    event.listen(engine, "after_cursor_execute", after_sql)
    event.listen(engine, "before_cursor_execute", before_sql)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reserved = pool.submit(reservation)
            assert daily_locked.wait(5)
            denied = pool.submit(admission)
            outcomes = reserved.result(timeout=15), denied.result(timeout=15)
    finally:
        admission_at_daily.set()
        event.remove(engine, "after_cursor_execute", after_sql)
        event.remove(engine, "before_cursor_execute", before_sql)
    assert outcomes == ("reserved", "ai.chain_transition_blocked"), outcomes
    with transaction(ctx) as db:
        run = db.get(AiRun, rejected.ai_run_id)
        assert run.status == "policy_blocked" and run.error_code == "ai.chain_transition_blocked"
        chain = db.scalars(select(ChainBudget)).one()
        assert chain.model_calls == 1 and chain.stopped_reason == "ai.chain_transition_blocked"
