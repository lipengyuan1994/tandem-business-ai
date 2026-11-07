"""Helpers for the AI tool tests (AI-06, AI-07): seeded demo, assistant contexts, row counts. Synthetic data only."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, text

from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.modules.ai.tests.helpers import insert_run, system_ctx
from app.modules.ai.tools.base import RunState
from app.modules.ai.tools.registry import execute_tool
from app.platform import clock
from app.platform.context import Context, assistant_context
from app.platform.ids import fixture_id

SEED_NOW = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)  # data/fixtures.json scenario_reference_now


@dataclass
class Demo:
    business: uuid.UUID
    other: uuid.UUID
    customers: dict[str, uuid.UUID]  # display name → id (Example Cycle Works)
    items: dict[str, uuid.UUID]  # catalog item name → id
    conversations: dict[str, uuid.UUID]  # visitor name or customer display name → id
    resources: dict[str, uuid.UUID]
    quote_request: uuid.UUID
    other_customers: dict[str, uuid.UUID]
    other_items: dict[str, uuid.UUID]
    other_conversations: dict[str, uuid.UUID]


def _names(owner_engine: Engine, sql: str, business: uuid.UUID) -> dict[str, uuid.UUID]:
    with owner_engine.connect() as conn:
        return {str(r[0]): r[1] for r in conn.execute(text(sql), {"b": business})}


_CONVERSATIONS = (
    "select coalesce(c.visitor_name, cu.display_name), c.id from conversations c "
    "left join customers cu on cu.id = c.customer_id where c.business_id = :b"
)


def seed_demo(owner_engine: Engine, at: datetime = SEED_NOW) -> Demo:
    """Load the original two-business scenario and keep its reference clock frozen."""
    from app.seed import seed

    clock.set_clock(lambda: at)
    seed(business_keys={"biz-cycle", "biz-garden"})
    business, other = fixture_id("biz-cycle"), fixture_id("biz-garden")
    customers = "select display_name, id from customers where business_id = :b"
    items = "select name, id from catalog_items where business_id = :b"
    with owner_engine.connect() as conn:
        quote_request = conn.execute(
            text("select id from quote_requests where business_id = :b"), {"b": business}
        ).scalar_one()
    return Demo(
        business=business,
        other=other,
        customers=_names(owner_engine, customers, business),
        items=_names(owner_engine, items, business),
        conversations=_names(owner_engine, _CONVERSATIONS, business),
        resources=_names(owner_engine, "select name, id from resources where business_id = :b", business),
        quote_request=quote_request,
        other_customers=_names(owner_engine, customers, other),
        other_items=_names(owner_engine, items, other),
        other_conversations=_names(owner_engine, _CONVERSATIONS, other),
    )


def assistant(
    business: uuid.UUID,
    permissions: frozenset[str] = AI_SYSTEM_PERMISSIONS,
    *,
    trigger_key: str | None = None,
    trigger_message_id: uuid.UUID | None = None,
    on_behalf_of_user_id: uuid.UUID | None = None,
) -> tuple[Context, RunState]:
    """A background-style assistant context with a recorded run, and a fresh run state for it."""
    key = trigger_key or f"test:{uuid.uuid4()}"
    run_id = insert_run(system_ctx(business), trigger_key=key)
    ctx = assistant_context(business, permissions, on_behalf_of_user_id=on_behalf_of_user_id, ai_run_id=run_id)
    return ctx, RunState(run_id=run_id, trigger_key=key, trigger_message_id=trigger_message_id)


def call(ctx: Context, state: RunState, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """execute_tool with only this tool allowed; the observation must be JSON (as the runner sends it)."""
    observation = execute_tool(ctx, name, args or {}, [name], state)
    json.dumps(observation)
    return observation


def ok(observation: dict[str, Any]) -> Any:
    assert observation["ok"] is True, observation
    return observation["data"]


def table_counts(owner_engine: Engine) -> dict[str, int]:
    """Row count of every table in the public schema."""
    with owner_engine.connect() as conn:
        tables = conn.execute(
            text("select tablename from pg_tables where schemaname = 'public' order by tablename")
        ).scalars()
        names = list(tables)
        return {t: int(conn.execute(text(f'select count(*) from "{t}"')).scalar_one()) for t in names}  # noqa: S608


def contact_values(owner_engine: Engine, business: uuid.UUID) -> set[str]:
    """Every customer contact value and visitor e-mail/phone stored for the business (lower case)."""
    with owner_engine.connect() as conn:
        values = set(
            conn.execute(text("select value from contact_points where business_id = :b"), {"b": business}).scalars()
        )
        for row in conn.execute(
            text("select visitor_email, visitor_phone from conversations where business_id = :b"), {"b": business}
        ):
            values.update(v for v in row if v)
    return {v.lower() for v in values}
