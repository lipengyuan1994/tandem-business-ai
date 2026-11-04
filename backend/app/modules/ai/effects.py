"""Effects: the holds and proposals a run created (contracts §4 `ai_effects`, §8 recovery, §10).

An effect row is written in the same transaction as the hold or proposal it records, so a retried attempt finds it
and replays the recorded result instead of creating a second one.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.canonical import canonical_json
from app.platform.context import Context

from .models import AiEffect

EffectKind = Literal["hold", "proposal"]


@dataclass(frozen=True)
class EffectRecord:
    effect_key: str
    kind: EffectKind
    ref_id: uuid.UUID
    run_id: uuid.UUID
    trigger_key: str
    created_at: datetime


def _record(row: AiEffect) -> EffectRecord:
    return EffectRecord(row.effect_key, row.kind, row.ref_id, row.run_id, row.trigger_key, row.created_at)  # type: ignore[arg-type]


def effect_key(tool: str, args: BaseModel | dict[str, Any], *, trigger_key: str | None = None) -> str:
    """sha256 of the tool name and the canonical JSON of its identifying arguments.

    For an args model, the identifying arguments are its `effect_fields` class attribute when present, else all fields.
    `execute_tool` passes the run's `trigger_key`, so retries of one trigger replay its effects while a later trigger
    (another message, another copilot turn) with the same arguments creates new ones.
    """
    if isinstance(args, BaseModel):
        values = args.model_dump(mode="python")
        fields: tuple[str, ...] | None = getattr(type(args), "effect_fields", None)
        if fields is not None:
            values = {f: values[f] for f in fields}
    else:
        values = dict(args)
    if trigger_key is not None:
        values = {"__trigger__": trigger_key, **values}
    return hashlib.sha256(tool.encode("utf-8") + b"\n" + canonical_json(values)).hexdigest()


def find_effect(db: Session, ctx: Context, effect_key: str) -> EffectRecord | None:
    row = db.scalar(select(AiEffect).where(AiEffect.business_id == ctx.business_id, AiEffect.effect_key == effect_key))
    return _record(row) if row is not None else None


def record_effect(
    db: Session,
    ctx: Context,
    *,
    run_id: uuid.UUID,
    trigger_key: str,
    effect_key: str,
    kind: EffectKind,
    ref_id: uuid.UUID,
) -> EffectRecord:
    """Insert the effect in the caller's transaction (the one that created the hold or proposal)."""
    row = AiEffect(
        business_id=ctx.business_id, run_id=run_id, trigger_key=trigger_key, effect_key=effect_key, kind=kind,
        ref_id=ref_id,
    )  # fmt: skip
    db.add(row)
    db.flush()
    return _record(row)


def effects_for_trigger(db: Session, ctx: Context, trigger_key: str) -> list[EffectRecord]:
    """Every effect of runs with this trigger key, oldest first (the runner rebuilds RunState from them)."""
    rows = db.scalars(
        select(AiEffect)
        .where(AiEffect.business_id == ctx.business_id, AiEffect.trigger_key == trigger_key)
        .order_by(AiEffect.created_at, AiEffect.id)
    )
    return [_record(r) for r in rows]
