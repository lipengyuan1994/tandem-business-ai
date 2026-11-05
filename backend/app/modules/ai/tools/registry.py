"""Tool registry and execution (docs/roadmap/ai/contracts.md §10, observation JSON §8)."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.scheduling import public as scheduling
from app.platform import clock
from app.platform.canonical import to_jsonable
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError

from .. import effects
from .base import RunState, Tool

_TOOLS: dict[str, Tool] = {}

MAX_OBSERVATION_CHARS = 4000
MAX_DETAIL_CHARS = 200
_REF_FIELD = {"hold": "hold_id", "propose": "proposal_id"}
_EFFECT_KIND: dict[str, effects.EffectKind] = {"hold": "hold", "propose": "proposal"}


def register_tool(tool: Tool) -> None:
    if tool.name in _TOOLS:
        raise ValueError(f"duplicate tool {tool.name}")
    if "business_id" in tool.args_model.model_fields:
        raise ValueError(f"tool {tool.name}: arguments must never include business_id")
    if tool.args_model.model_config.get("extra") != "forbid":
        raise ValueError(f"tool {tool.name}: arguments model must forbid extra fields")
    _TOOLS[tool.name] = tool


def get_tool(name: str) -> Tool:
    return _TOOLS[name]


def tool_catalog(names: list[str]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "arguments": t.args_model.model_json_schema()}
        for t in (get_tool(n) for n in names)
    ]


# Additional permissions for data looked up by action tools. Keep this check in the executor as well as the
# model catalogue: a guessed tool name and an idempotent replay must obey the same authorization rules.
_LOOKUP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "hold_slot": ("crm.customer.read", "catalog.item.read", "scheduling.appointment.read"),
    "propose_booking": (
        "scheduling.appointment.read",
        "catalog.item.read",
        "crm.customer.read",
        "conversations.thread.read",
    ),
    "propose_reply": ("conversations.thread.read",),
    "propose_reorder": ("inventory.stock.read",),
    "propose_payment_reminder": ("sales.document.read",),
    "propose_quote_request": ("purchasing.po.read", "inventory.stock.read"),
    "propose_supplier_quote": ("purchasing.po.read",),
    "propose_po_from_quote": ("purchasing.po.read",),
    "propose_supplier_followup": ("purchasing.po.read",),
}


def required_permissions(tool: Tool) -> tuple[str, ...]:
    return (tool.permission, *_LOOKUP_PERMISSIONS.get(tool.name, ()))


def authorized_tools(ctx: Context, names: list[str]) -> list[str]:
    return [name for name in names if name in _TOOLS and all(ctx.has(p) for p in required_permissions(_TOOLS[name]))]


# ------------------------------------------------------------------------------------------- observations
def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def error_observation(code: str, detail: str | None = None) -> dict[str, Any]:
    return {"ok": False, "error": code, "detail": (detail or "")[:MAX_DETAIL_CHARS]}


def _longest_list(value: Any) -> list[Any] | None:
    """The non-empty list inside `value` whose JSON is largest (the best one to shorten)."""
    best: tuple[int, list[Any]] | None = None
    stack = [value]
    while stack:
        item = stack.pop()
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        if isinstance(item, list) and item:
            size = _size(item)
            if best is None or size > best[0]:
                best = (size, item)
        stack.extend(c for c in children if isinstance(c, dict | list))
    return best[1] if best else None


def ok_observation(data: Any) -> dict[str, Any]:
    """`{"ok": true, "data": …}`, at most 4,000 characters of JSON; longer data loses list items from the end (or,
    without lists, is cut as text) and the observation says `"truncated": true`."""
    data = to_jsonable(data)
    observation = {"ok": True, "data": data}
    if _size(observation) <= MAX_OBSERVATION_CHARS:
        return observation
    data = copy.deepcopy(data)
    observation = {"ok": True, "data": data, "truncated": True}
    while _size(observation) > MAX_OBSERVATION_CHARS:
        longest = _longest_list(data)
        if longest is None:  # no list left to shorten: cut the JSON text (escaping can grow it, hence the loop)
            text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            observation["data"] = text
            while (excess := _size(observation) - MAX_OBSERVATION_CHARS) > 0:
                text = text[: len(text) - excess]
                observation["data"] = text
            break
        longest.pop()
    return observation


def _validation_detail(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['type']}" for e in exc.errors())


# ------------------------------------------------------------------------------------------- execution
def _replay(tool: Tool, args: BaseModel, state: RunState, record: effects.EffectRecord) -> dict[str, Any]:
    _remember(tool, args, state, record.effect_key, record.ref_id)
    return ok_observation({_REF_FIELD[tool.kind]: record.ref_id, "replayed": True})


def _remember(tool: Tool, args: BaseModel, state: RunState, key: str, ref_id: uuid.UUID) -> None:
    if tool.kind == "propose":
        state.proposals[key] = ref_id
    else:
        state.holds.setdefault(ref_id, args.model_dump())


def execute_tool(
    ctx: Context, name: str, raw_args: dict[str, Any], allowed: list[str], state: RunState
) -> dict[str, Any]:
    """Run one tool call in its own transaction and return the observation for the model.

    Not allowed or unknown tool, missing permission, invalid arguments and domain errors all become
    `{"ok": false, ...}` observations. Hold and propose tools are idempotent per effect key: a key already in the
    run state or in `ai_effects` returns the recorded id; otherwise the effect is recorded in the same transaction.
    """
    if name not in allowed or name not in _TOOLS:
        return error_observation("ai.tool_not_allowed", name)
    tool = _TOOLS[name]
    for permission in required_permissions(tool):
        if not ctx.has(permission):
            return error_observation("auth.permission_denied", permission)
    try:
        args = tool.args_model.model_validate(raw_args)
    except ValidationError as exc:
        return error_observation("platform.validation_error", _validation_detail(exc))
    if tool.kind == "read":
        try:
            with transaction(ctx) as db:
                data = tool.run(db, ctx, args, state)
        except DomainError as exc:
            return error_observation(exc.code, exc.detail)
        return ok_observation(data)
    return _execute_effect(ctx, tool, args, state)


def _intake_claim_key(tool: Tool, state: RunState) -> str | None:
    if tool.name not in {"propose_booking", "propose_reply"} or not state.trigger_key.startswith("intake:"):
        return None
    return effects.effect_key("intake_outcome", {}, trigger_key=state.trigger_key)


def _live_hold(db: Session, ctx: Context, ref_id: uuid.UUID) -> bool:
    try:
        hold = scheduling.get_appointment(db, ctx, ref_id)
    except DomainError:
        return False
    return hold.status == "held" and hold.hold_expires_at is not None and hold.hold_expires_at > clock.now()


def _hold_key(
    db: Session,
    ctx: Context,
    tool: Tool,
    args: BaseModel,
    state: RunState,
    key: str,
) -> tuple[str, effects.EffectRecord | None, bool]:
    recorded = effects.find_effect(db, ctx, key)
    while recorded is not None and tool.kind == "hold" and not _live_hold(db, ctx, recorded.ref_id):
        # Expiry must be an observation, never a successful replay. Remember that observation in this run only;
        # a restarted run will observe it once again before it can reacquire the slot.
        if not state.holds.get(recorded.ref_id, {}).get("expired"):
            state.holds[recorded.ref_id] = {"expired": True}
            return key, recorded, True
        key = effects.effect_key(f"{tool.name}:after:{recorded.ref_id}", args, trigger_key=state.trigger_key)
        recorded = effects.find_effect(db, ctx, key)
    return key, recorded, False


def _execute_effect(ctx: Context, tool: Tool, args: BaseModel, state: RunState) -> dict[str, Any]:
    key = effects.effect_key(tool.name, args, trigger_key=state.trigger_key)
    claim = _intake_claim_key(tool, state)
    if tool.kind == "propose" and key in state.proposals:
        return ok_observation({"proposal_id": state.proposals[key], "replayed": True})
    try:
        with transaction(ctx) as db:
            key, recorded, expired = _hold_key(db, ctx, tool, args, state, key)
            if expired:
                return error_observation("ai.hold_expired", "The hold expired or was released; call hold_slot again.")
            if recorded is not None:
                return _replay(tool, args, state, recorded)
            if claim is not None and any(
                effect.kind == "proposal" for effect in effects.effects_for_trigger(db, ctx, state.trigger_key)
            ):
                # Also recognize effects created before shared claims were introduced. Exact retries already
                # replayed above; a different proposal is refused regardless of the old proposal's status.
                return error_observation("ai.intake_outcome_exists", "This inbound message already has a proposal.")
            data = tool.run(db, ctx, args, state)
            ref_id = uuid.UUID(str(data[_REF_FIELD[tool.kind]]))
            effects.record_effect(
                db,
                ctx,
                run_id=state.run_id,
                trigger_key=state.trigger_key,
                effect_key=key,
                kind=_EFFECT_KIND[tool.kind],
                ref_id=ref_id,
            )
            if claim is not None:
                # A shared unique key for booking OR reply, in the proposal transaction. Losing concurrent
                # transactions roll back the proposal, binding and normal effect together.
                effects.record_effect(
                    db,
                    ctx,
                    run_id=state.run_id,
                    trigger_key=state.trigger_key,
                    effect_key=claim,
                    kind="proposal",
                    ref_id=ref_id,
                )
    except DomainError as exc:
        return error_observation(exc.code, exc.detail)
    except IntegrityError:
        # The losing transaction is rolled back before inspecting the winner's committed effects.
        with transaction(ctx) as db:
            recorded = effects.find_effect(db, ctx, key)
            if recorded is not None:
                if tool.kind == "hold" and not _live_hold(db, ctx, recorded.ref_id):
                    state.holds[recorded.ref_id] = {"expired": True}
                    return error_observation("ai.hold_expired", "The hold expired or was released; hold again.")
                return _replay(tool, args, state, recorded)
            if claim is not None and effects.find_effect(db, ctx, claim) is not None:
                return error_observation("ai.intake_outcome_exists", "This inbound message already has a proposal.")
        raise
    _remember(tool, args, state, key, ref_id)
    return ok_observation(data)


# ------------------------------------------------------------------------------------------- built-in tools
def _register_builtin_tools() -> None:
    """Register every tool of the area files (AI-06 read tools, AI-07 hold and propose tools) once, at import."""
    from . import catalog, conversations, crm, dashboard, inventory, proposals, purchasing, sales, scheduling

    for area in (conversations, crm, catalog, scheduling, inventory, purchasing, sales, dashboard, proposals):
        for tool in area.TOOLS:
            register_tool(tool)


_register_builtin_tools()
