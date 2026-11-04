"""Run recording (contracts §4, §8). Every function owns one short `transaction(ctx)`, so nothing is held open across
a model call. Prompt and completion text are never stored (§2): steps carry redacted arguments and a short summary of
ids, counts and error codes; `ai_runs.result` holds only the use case's validated output.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction

from . import usage
from .agent.prompts import Prompt
from .agent.protocol import RunResult, RunStatus
from .gateway.pricing import cost_micros
from .gateway.routes import RouteEntry
from .gateway.types import ChatResult
from .models import AiRun, AiRunStep, AiUsageDaily
from .schemas import UseCaseId

MAX_RESULT_TEXT = 2000
MAX_SUMMARY = 500
StepKind = Literal["tool_call", "final", "repair", "completion"]


class RunMeta(BaseModel):
    use_case: UseCaseId
    prompt: Prompt
    trigger_type: Literal["event", "job", "api", "cron"]
    trigger_key: str
    on_behalf_of_user_id: uuid.UUID | None = None


class StepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_no: int
    kind: StepKind
    tool: str | None = None
    arguments_redacted: dict[str, Any] = {}
    result_summary: str = Field(default="", max_length=MAX_SUMMARY)
    error_code: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    provider: str | None = None
    model: str | None = None


# ------------------------------------------------------------------------------------------------ redaction
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
_NUMBER_RE = re.compile(r"^-?\d{1,15}(\.\d{1,6})?$")


def redact_value(value: Any, depth: int = 0) -> Any:
    """Keep ids, numbers, booleans and ISO dates; replace any other text by its length (no free text, §2)."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if _UUID_RE.match(value) or _DATE_RE.match(value) or _NUMBER_RE.match(value):
            return value
        return f"<text {len(value)} chars>"
    if depth >= 3:
        return "<nested>"
    if isinstance(value, dict):
        return {str(k)[:40]: redact_value(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, list | tuple):
        items = [redact_value(v, depth + 1) for v in list(value)[:10]]
        return items if len(value) <= 10 else [*items, f"<{len(value) - 10} more>"]
    return f"<{type(value).__name__}>"


def redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_value(arguments)
    return redacted if isinstance(redacted, dict) else {}


def summarize_observation(observation: dict[str, Any]) -> str:
    """`ok` with the ids it returned and its size, or `error <code>`; never the data itself."""
    if not observation.get("ok"):
        return f"error {str(observation.get('error', 'unknown'))[:100]}"[:MAX_SUMMARY]
    data = observation.get("data")
    parts = ["ok"]
    if isinstance(data, dict):
        for key in ("proposal_id", "hold_id"):
            if key in data:
                parts.append(f"{key}={data[key]}")
        if data.get("replayed"):
            parts.append("replayed")
        lists = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        parts.extend(f"{k}={n} items" for k, n in list(lists.items())[:5])
    elif isinstance(data, list):
        parts.append(f"{len(data)} items")
    if observation.get("truncated"):
        parts.append("truncated")
    return "; ".join(parts)[:MAX_SUMMARY]


def _cap_text(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_RESULT_TEXT]
    if isinstance(value, dict):
        return {k: _cap_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_cap_text(v) for v in value]
    return value


# ------------------------------------------------------------------------------------------------ writes
def _add_usage(db: Session, ctx: Context, *, input_tokens: int, output_tokens: int, cost: int, runs: int) -> None:
    stmt = insert(AiUsageDaily).values(
        business_id=ctx.business_id, day=usage.utc_today(), input_tokens=input_tokens, output_tokens=output_tokens,
        cost_micros=cost, runs=runs,
    )  # fmt: skip
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[AiUsageDaily.business_id, AiUsageDaily.day],
            set_={
                "input_tokens": AiUsageDaily.input_tokens + stmt.excluded.input_tokens,
                "output_tokens": AiUsageDaily.output_tokens + stmt.excluded.output_tokens,
                "cost_micros": AiUsageDaily.cost_micros + stmt.excluded.cost_micros,
                "runs": AiUsageDaily.runs + stmt.excluded.runs,
                "updated_at": clock.now(),
            },
        )
    )


def close_stale_runs(ctx: Context, trigger_key: str, older_than_s: int) -> int:
    """Runs of this trigger still `running` after `older_than_s` belong to a crashed attempt: `failed`,
    `ai.interrupted`. Returns how many were closed."""
    cutoff = clock.now() - timedelta(seconds=older_than_s)
    with transaction(ctx) as db:
        closed = db.execute(
            update(AiRun)
            .where(
                AiRun.business_id == ctx.business_id,
                AiRun.trigger_key == trigger_key,
                AiRun.status == "running",
                AiRun.started_at < cutoff,
            )
            .values(status="failed", error_code="ai.interrupted", finished_at=clock.now())
            .returning(AiRun.id)
        ).all()
    return len(closed)


def open_run(ctx: Context, spec_or_meta: RunMeta) -> uuid.UUID:
    """Insert the run (`running`) with id `ctx.ai_run_id` (set by the context factory) and count it for today."""
    meta = spec_or_meta
    run_id = ctx.ai_run_id or uuid.uuid4()
    with transaction(ctx) as db:
        db.add(
            AiRun(
                id=run_id, business_id=ctx.business_id, use_case=meta.use_case, trigger_type=meta.trigger_type,
                trigger_key=meta.trigger_key, on_behalf_of_user_id=meta.on_behalf_of_user_id,
                prompt_name=meta.prompt.name, prompt_version=meta.prompt.version, model_class=meta.prompt.model_class,
                status="running", started_at=clock.now(),
            )
        )  # fmt: skip
        db.flush()
        _add_usage(db, ctx, input_tokens=0, output_tokens=0, cost=0, runs=1)
    return run_id


def record_step(ctx: Context, run_id: uuid.UUID, step: StepRecord) -> None:
    with transaction(ctx) as db:
        db.add(AiRunStep(business_id=ctx.business_id, run_id=run_id, **step.model_dump()))
        db.flush()
        db.execute(
            update(AiRun).where(AiRun.business_id == ctx.business_id, AiRun.id == run_id).values(steps=AiRun.steps + 1)
        )


def record_usage_now(ctx: Context, entry: RouteEntry, result: ChatResult) -> None:
    """After every model call: today's usage (budget checks see it at once), the run's counters and the 80 % notice.
    The run is `ctx.ai_run_id`."""
    cost = cost_micros(entry, result.usage)
    with transaction(ctx) as db:
        _add_usage(
            db, ctx, input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens, cost=cost, runs=0
        )
        if ctx.ai_run_id is not None:
            db.execute(
                update(AiRun)
                .where(AiRun.business_id == ctx.business_id, AiRun.id == ctx.ai_run_id)
                .values(
                    input_tokens=AiRun.input_tokens + result.usage.input_tokens,
                    output_tokens=AiRun.output_tokens + result.usage.output_tokens,
                    cost_micros=AiRun.cost_micros + cost,
                    latency_ms=AiRun.latency_ms + result.latency_ms,
                    provider=result.provider,
                    model=result.model,
                )
            )
        usage.maybe_notify_80_percent(db, ctx)


def close_run(
    ctx: Context,
    run_id: uuid.UUID,
    status: RunStatus,
    *,
    error_code: str | None = None,
    needs_human: bool = False,
    result: dict[str, Any] | None = None,
) -> None:
    with transaction(ctx) as db:
        db.execute(
            update(AiRun)
            .where(AiRun.business_id == ctx.business_id, AiRun.id == run_id)
            .values(
                status=status,
                error_code=error_code,
                needs_human=needs_human,
                result=_cap_text(result or {}),
                finished_at=clock.now(),
            )
        )


def record_early_result(ctx: Context, meta: RunMeta, *, needs_human: bool, result: dict[str, Any]) -> RunResult:
    """A run with 0 steps for a decision made in code without a model call (ambiguous customer, duplicate, safety
    flag): `succeeded`. The run id is `ctx.ai_run_id`."""
    run_id = open_run(ctx, meta)
    close_run(ctx, run_id, "succeeded", needs_human=needs_human, result=result)
    return RunResult(run_id=run_id, status="succeeded", final=None, proposal_ids=[])


__all__ = [
    "RunMeta",
    "StepRecord",
    "close_run",
    "close_stale_runs",
    "open_run",
    "record_early_result",
    "record_step",
    "record_usage_now",
    "redact_arguments",
    "summarize_observation",
]
