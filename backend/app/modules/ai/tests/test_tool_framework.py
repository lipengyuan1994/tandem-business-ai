"""AI-01: tool framework (contracts §10) tested with dummy tools only; no real tools, no model calls."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.modules.ai import effects
from app.modules.ai.models import AiEffect
from app.modules.ai.tests.helpers import insert_run, system_ctx
from app.modules.ai.tools import registry as tool_registry
from app.modules.ai.tools.base import RunState, Tool
from app.modules.ai.tools.registry import execute_tool, get_tool, register_tool, tool_catalog
from app.modules.org.testing import make_business
from app.platform.context import Context, assistant_context
from app.platform.db import transaction
from app.platform.errors import NotFound
from app.platform.ids import new_id


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word: str = Field(min_length=1, max_length=40)
    repeat: int = Field(default=1, ge=1, le=2000)


class NoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect_fields: ClassVar[tuple[str, ...]] = ("name",)
    name: str = Field(min_length=1, max_length=60)
    reason: str = Field(default="", max_length=100)  # not identifying: a retry with other words is the same effect


def _echo(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, EchoArgs)
    if args.word == "missing":
        raise NotFound("crm.customer_not_found", detail="no such customer")
    return {"words": [args.word] * args.repeat, "business": "hidden-from-args"}


def _propose_note(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    """Stands in for a propose tool: writes a business row (the 'proposal') and returns its id."""
    assert isinstance(args, NoteArgs)
    row_id = new_id()
    db.execute(
        text("insert into test_items (id, business_id, name) values (:id, :b, :n)"),
        {"id": row_id, "b": ctx.business_id, "n": args.name},
    )
    return {"proposal_id": row_id, "title": f"Note {args.name}"}


ECHO = Tool(name="test_echo", description="Echo a word.", kind="read", permission="crm.customer.read",
            args_model=EchoArgs, run=_echo)  # fmt: skip
NOTE = Tool(name="test_propose_note", description="Propose a note.", kind="propose", permission="ai.assistant.use",
            args_model=NoteArgs, run=_propose_note)  # fmt: skip


@pytest.fixture(autouse=True)
def tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(tool_registry, "_TOOLS", {})
    register_tool(ECHO)
    register_tool(NOTE)
    yield


def _assistant(business: uuid.UUID, run_id: uuid.UUID, *perms: str) -> Context:
    return assistant_context(business, frozenset(perms), on_behalf_of_user_id=None, ai_run_id=run_id)


def _items(owner_engine: Engine, business: uuid.UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(text("select count(*) from test_items where business_id = :b"), {"b": business}).scalar_one()
        )


def _effects(owner_engine: Engine, business: uuid.UUID) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(text("select count(*) from ai_effects where business_id = :b"), {"b": business}).scalar_one()
        )


# ----------------------------------------------------------------------------------------------- registry


def test_register_tool_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="test_echo"):
        register_tool(ECHO)
    assert get_tool("test_echo") is ECHO
    with pytest.raises(KeyError):
        get_tool("no_such_tool")


def test_register_tool_rejects_business_id_argument_and_open_models() -> None:
    class Scoped(BaseModel):
        model_config = ConfigDict(extra="forbid")
        business_id: uuid.UUID

    class Open(BaseModel):
        word: str

    for i, model in enumerate((Scoped, Open)):
        with pytest.raises(ValueError):
            register_tool(Tool(name=f"bad_{i}", description="x", kind="read", permission="crm.customer.read",
                               args_model=model, run=_echo))  # fmt: skip


def test_tool_catalog_lists_names_descriptions_and_argument_schemas() -> None:
    catalog = tool_catalog(["test_echo", "test_propose_note"])
    assert [t["name"] for t in catalog] == ["test_echo", "test_propose_note"]
    assert catalog[0]["description"] == "Echo a word."
    assert catalog[0]["arguments"] == EchoArgs.model_json_schema()
    assert catalog[0]["arguments"]["additionalProperties"] is False
    with pytest.raises(KeyError):
        tool_catalog(["no_such_tool"])


# ----------------------------------------------------------------------------------------------- execute


def test_execute_tool_observation_shapes() -> None:
    business = make_business()
    run_id = insert_run(system_ctx(business))
    ctx = _assistant(business, run_id, "crm.customer.read")
    state = RunState(run_id=run_id, trigger_key="test:1")

    ok = execute_tool(ctx, "test_echo", {"word": "brake"}, ["test_echo"], state)
    assert ok == {"ok": True, "data": {"words": ["brake"], "business": "hidden-from-args"}}

    domain = execute_tool(ctx, "test_echo", {"word": "missing"}, ["test_echo"], state)
    assert domain == {"ok": False, "error": "crm.customer_not_found", "detail": "no such customer"}

    invalid = execute_tool(ctx, "test_echo", {"word": "brake", "business_id": str(business)}, ["test_echo"], state)
    assert invalid["ok"] is False and invalid["error"] == "platform.validation_error"
    assert "business_id" in invalid["detail"] and len(invalid["detail"]) <= 200

    not_allowed = execute_tool(ctx, "test_echo", {"word": "brake"}, ["test_propose_note"], state)
    assert not_allowed == {"ok": False, "error": "ai.tool_not_allowed", "detail": "test_echo"}
    unknown = execute_tool(ctx, "no_such_tool", {}, ["no_such_tool"], state)
    assert unknown["ok"] is False and unknown["error"] == "ai.tool_not_allowed"

    big = execute_tool(ctx, "test_echo", {"word": "brake-pads", "repeat": 1000}, ["test_echo"], state)
    assert big["ok"] is True and big["truncated"] is True
    assert 0 < len(big["data"]["words"]) < 1000
    assert len(json.dumps(big, separators=(",", ":"))) <= 4000


def test_execute_tool_permission_denied(owner_engine: Engine) -> None:
    business = make_business()
    run_id = insert_run(system_ctx(business))
    ctx = _assistant(business, run_id, "catalog.item.read")  # neither crm.customer.read nor ai.assistant.use
    state = RunState(run_id=run_id, trigger_key="test:1")
    assert execute_tool(ctx, "test_echo", {"word": "brake"}, ["test_echo"], state) == {
        "ok": False, "error": "auth.permission_denied", "detail": "crm.customer.read",
    }  # fmt: skip
    denied = execute_tool(ctx, "test_propose_note", {"name": "x"}, ["test_propose_note"], state)
    assert denied["error"] == "auth.permission_denied"
    assert _items(owner_engine, business) == 0 and _effects(owner_engine, business) == 0


def test_propose_effect_recorded_in_same_transaction_and_replayed(
    owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    business = make_business()
    run_id = insert_run(system_ctx(business), trigger_key="test:1")
    ctx = _assistant(business, run_id, "ai.assistant.use")
    allowed = ["test_propose_note"]

    # The effect row and the proposal it records commit together: when recording fails, the proposal is gone too.
    def broken_record(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("effect write failed")

    with monkeypatch.context() as m:
        m.setattr(effects, "record_effect", broken_record)
        with pytest.raises(RuntimeError):
            execute_tool(
                ctx, "test_propose_note", {"name": "pads"}, allowed, RunState(run_id=run_id, trigger_key="test:1")
            )
    assert _items(owner_engine, business) == 0 and _effects(owner_engine, business) == 0

    state = RunState(run_id=run_id, trigger_key="test:1")
    first = execute_tool(ctx, "test_propose_note", {"name": "pads", "reason": "low"}, allowed, state)
    assert first["ok"] is True
    proposal_id = uuid.UUID(first["data"]["proposal_id"])
    key = effects.effect_key("test_propose_note", NoteArgs(name="pads"), trigger_key="test:1")
    assert state.proposals == {key: proposal_id}
    assert _items(owner_engine, business) == 1 and _effects(owner_engine, business) == 1
    with transaction(ctx) as db:
        recorded = effects.find_effect(db, ctx, key)
        assert recorded is not None
        assert (recorded.kind, recorded.ref_id, recorded.run_id, recorded.trigger_key) == (
            "proposal", proposal_id, run_id, "test:1",
        )  # fmt: skip
        assert [e.ref_id for e in effects.effects_for_trigger(db, ctx, "test:1")] == [proposal_id]
        assert effects.effects_for_trigger(db, ctx, "test:2") == []

    # Same run: replayed from the run state. Non-identifying arguments do not matter.
    again = execute_tool(ctx, "test_propose_note", {"name": "pads", "reason": "other words"}, allowed, state)
    assert again == {"ok": True, "data": {"proposal_id": str(proposal_id), "replayed": True}}
    # A retried attempt starts with an empty state: replayed from ai_effects.
    fresh = RunState(run_id=run_id, trigger_key="test:1")
    retried = execute_tool(ctx, "test_propose_note", {"name": "pads"}, allowed, fresh)
    assert retried == again
    assert fresh.proposals == {key: proposal_id}
    assert _items(owner_engine, business) == 1 and _effects(owner_engine, business) == 1

    # Another identifying argument is another effect.
    other = execute_tool(ctx, "test_propose_note", {"name": "chain"}, allowed, fresh)
    assert other["ok"] is True and other["data"]["proposal_id"] != str(proposal_id)
    assert _items(owner_engine, business) == 2 and _effects(owner_engine, business) == 2


def test_later_trigger_with_same_arguments_creates_a_new_effect(owner_engine: Engine) -> None:
    """A new message or copilot turn is not a retry: it must not get an earlier trigger's (maybe closed) proposal."""
    business = make_business()
    first_run = insert_run(system_ctx(business), trigger_key="test:1")
    second_run = insert_run(system_ctx(business), trigger_key="test:2")
    allowed = ["test_propose_note"]
    first = execute_tool(_assistant(business, first_run, "ai.assistant.use"), "test_propose_note", {"name": "pads"},
                         allowed, RunState(first_run, "test:1"))  # fmt: skip
    second = execute_tool(_assistant(business, second_run, "ai.assistant.use"), "test_propose_note", {"name": "pads"},
                          allowed, RunState(second_run, "test:2"))  # fmt: skip
    assert second["ok"] is True and "replayed" not in second["data"]
    assert second["data"]["proposal_id"] != first["data"]["proposal_id"]
    assert _items(owner_engine, business) == 2 and _effects(owner_engine, business) == 2


def test_concurrent_duplicate_effect_replays_the_winner(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two attempts racing on one effect: the loser's proposal rolls back and it returns the winner's."""
    business = make_business()
    run_id = insert_run(system_ctx(business), trigger_key="test:1")
    ctx = _assistant(business, run_id, "ai.assistant.use")
    allowed = ["test_propose_note"]
    first = execute_tool(ctx, "test_propose_note", {"name": "pads"}, allowed, RunState(run_id, "test:1"))

    real_find = effects.find_effect
    calls = {"n": 0}

    def blind_first_lookup(db: Session, ctx: Context, key: str) -> effects.EffectRecord | None:
        calls["n"] += 1
        return None if calls["n"] == 1 else real_find(db, ctx, key)

    monkeypatch.setattr(effects, "find_effect", blind_first_lookup)
    state = RunState(run_id=run_id, trigger_key="test:1")
    loser = execute_tool(ctx, "test_propose_note", {"name": "pads"}, allowed, state)
    assert loser == {"ok": True, "data": {"proposal_id": first["data"]["proposal_id"], "replayed": True}}
    assert _items(owner_engine, business) == 1 and _effects(owner_engine, business) == 1


def test_effects_are_business_scoped(owner_engine: Engine) -> None:
    a, b = make_business("example-cycle-works"), make_business("example-garden-store")
    run_a, run_b = insert_run(system_ctx(a), trigger_key="test:1"), insert_run(system_ctx(b), trigger_key="test:1")
    args = {"name": "pads"}
    first = execute_tool(_assistant(a, run_a, "ai.assistant.use"), "test_propose_note", args, ["test_propose_note"],
                         RunState(run_a, "test:1"))  # fmt: skip
    second = execute_tool(_assistant(b, run_b, "ai.assistant.use"), "test_propose_note", args, ["test_propose_note"],
                          RunState(run_b, "test:1"))  # fmt: skip
    assert "replayed" not in second["data"]
    assert first["data"]["proposal_id"] != second["data"]["proposal_id"]
    with transaction(system_ctx(b)) as db:
        assert db.scalar(select(func.count()).select_from(AiEffect)) == 1


def test_effect_key_is_stable_and_uses_identifying_fields() -> None:
    a = effects.effect_key("test_propose_note", NoteArgs(name="pads", reason="one"))
    b = effects.effect_key("test_propose_note", NoteArgs(name="pads", reason="two"))
    c = effects.effect_key("other_tool", NoteArgs(name="pads"))
    d = effects.effect_key("test_echo", EchoArgs(word="x"))
    e = effects.effect_key("test_echo", EchoArgs(word="x", repeat=2))
    assert a == b and a != c and d != e
    t1 = effects.effect_key("test_propose_note", NoteArgs(name="pads"), trigger_key="intake:1")
    t2 = effects.effect_key("test_propose_note", NoteArgs(name="pads"), trigger_key="intake:2")
    assert t1 != t2 and t1 != a
    assert len(a) == 64 and int(a, 16) >= 0
