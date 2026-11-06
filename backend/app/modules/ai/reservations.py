"""Short, committed reservations before each provider attempt; no transaction spans inference.

A crash retains its reservation. Refunds only follow a reported successful usage value; unknown usage
(including adapter exceptions) remains charged. Dates are captured at reservation, including across UTC rollover.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction

from . import team_budget
from .budget_models import CostPeriod, PublicAllowance
from .errors import AiError
from .gateway.pricing import cost_micros
from .gateway.routes import RouteEntry
from .gateway.types import Adapter, ChatRequest, ChatResult, TranscriptionRequest, Usage
from .models import AiRun, AiUsageDaily
from .usage import effective_budget, maybe_notify_80_percent

_lock = threading.Lock()
_active = 0


@contextmanager
def model_slot() -> Iterator[None]:
    """Fail fast rather than occupy worker threads waiting for another model call."""
    global _active
    with _lock:
        if _active >= get_settings().ai_max_concurrent_calls:
            raise AiError("ai.provider_unavailable")
        _active += 1
    try:
        yield
    finally:
        with _lock:
            _active -= 1


@dataclass(frozen=True)
class Reservation:
    day: date
    month: date
    input_tokens: int
    output_tokens: int
    cost: int
    public: bool


def conservative_input(req: ChatRequest) -> int:
    """One token per UTF-8 byte plus framing/schema overhead; deliberately above the bytes/4 UI estimate."""
    schema = json.dumps(req.json_schema or {}, ensure_ascii=False)
    return 256 + len(schema.encode()) + sum(len(m.content.encode()) + len(m.role) + 32 for m in req.messages)


def reserve(ctx: Context, entry: RouteEntry, req: ChatRequest | TranscriptionRequest) -> Reservation:
    day = clock.now().astimezone(UTC).date()
    month = day.replace(day=1)
    audio = isinstance(req, TranscriptionRequest)
    input_tokens = 0 if isinstance(req, TranscriptionRequest) else conservative_input(req)
    output_tokens = 0 if isinstance(req, TranscriptionRequest) else req.max_output_tokens
    tokens = input_tokens + output_tokens
    cost = int(
        (
            Decimal(str(entry.price_in_per_m)) * input_tokens + Decimal(str(entry.price_out_per_m)) * output_tokens
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    if audio:
        cost = cost_micros(entry, Usage(input_tokens=0, output_tokens=0, audio_seconds=120), round_up=True)
    public = req.use_case in {"intake", "safety"}
    result = Reservation(day, month, input_tokens, output_tokens, cost, public)
    s = get_settings()
    # One transaction for all counters: a failed conditional update rolls back every reservation.
    with transaction(ctx) as db:
        for kind, period, limit in (
            ("month", month, s.ai_monthly_cost_limit_usd),
            ("day", day, s.ai_daily_cost_limit_usd),
        ):
            db.execute(insert(CostPeriod).values(kind=kind, period=period, cost_micros=0).on_conflict_do_nothing())
            claimed = db.scalar(
                update(CostPeriod)
                .where(
                    CostPeriod.kind == kind,
                    CostPeriod.period == period,
                    CostPeriod.cost_micros + cost <= int(limit * 1_000_000),
                )
                .values(cost_micros=CostPeriod.cost_micros + cost)
                .returning(CostPeriod.kind)
            )
            if claimed is None:
                raise AiError("ai.budget_exceeded")
        budget = effective_budget(db, ctx)
        if budget <= 0:
            raise AiError("ai.budget_exceeded")
        db.execute(insert(AiUsageDaily).values(business_id=ctx.business_id, day=day).on_conflict_do_nothing())
        claimed = db.scalar(
            update(AiUsageDaily)
            .where(
                AiUsageDaily.business_id == ctx.business_id,
                AiUsageDaily.day == day,
                AiUsageDaily.input_tokens + AiUsageDaily.output_tokens + tokens <= budget,
            )
            .values(
                input_tokens=AiUsageDaily.input_tokens + input_tokens,
                output_tokens=AiUsageDaily.output_tokens + output_tokens,
                cost_micros=AiUsageDaily.cost_micros + cost,
            )
            .returning(AiUsageDaily.id)
        )
        if claimed is None:
            raise AiError("ai.budget_exceeded")
        if public:
            period = datetime.combine(day, datetime.min.time(), UTC)
            db.execute(
                insert(PublicAllowance)
                .values(
                    business_id=ctx.business_id,
                    kind="tokens",
                    period=period,
                    used=0,
                )
                .on_conflict_do_nothing()
            )
            claimed = db.scalar(
                update(PublicAllowance)
                .where(
                    PublicAllowance.business_id == ctx.business_id,
                    PublicAllowance.kind == "tokens",
                    PublicAllowance.period == period,
                    PublicAllowance.used + tokens <= min(s.ai_public_daily_token_budget_per_business, budget // 4),
                )
                .values(used=PublicAllowance.used + tokens)
                .returning(PublicAllowance.id)
            )
            if claimed is None:
                raise AiError("ai.budget_exceeded")
        maybe_notify_80_percent(db, ctx)
        team_budget.reserve_model(db, ctx, cost, day)
    return result


def reconcile(ctx: Context, r: Reservation, entry: RouteEntry, result: ChatResult | None) -> None:
    # Providers/adapters currently represent absent usage as 0/0. Treat it as unknown, never as a free call.
    reported = result is not None and (
        result.usage.audio_seconds is not None or (result.usage.input_tokens > 0 and result.usage.output_tokens >= 0)
    )
    usage = (
        result.usage
        if reported and result is not None
        else Usage(input_tokens=r.input_tokens, output_tokens=r.output_tokens)
    )
    cost = cost_micros(entry, usage) if reported else r.cost
    delta_in, delta_out, delta_cost = (
        usage.input_tokens - r.input_tokens,
        usage.output_tokens - r.output_tokens,
        cost - r.cost,
    )
    with transaction(ctx) as db:
        # Same lock order as reserve (month -> day -> business -> public).
        for kind, period in (("month", r.month), ("day", r.day)):
            db.execute(
                update(CostPeriod)
                .where(CostPeriod.kind == kind, CostPeriod.period == period)
                .values(cost_micros=CostPeriod.cost_micros + delta_cost)
            )
        db.execute(
            update(AiUsageDaily)
            .where(
                AiUsageDaily.business_id == ctx.business_id,
                AiUsageDaily.day == r.day,
            )
            .values(
                input_tokens=AiUsageDaily.input_tokens + delta_in,
                output_tokens=AiUsageDaily.output_tokens + delta_out,
                cost_micros=AiUsageDaily.cost_micros + delta_cost,
            )
        )
        if r.public:
            db.execute(
                update(PublicAllowance)
                .where(
                    PublicAllowance.business_id == ctx.business_id,
                    PublicAllowance.kind == "tokens",
                    PublicAllowance.period == datetime.combine(r.day, datetime.min.time(), UTC),
                )
                .values(used=PublicAllowance.used + delta_in + delta_out)
            )
        if ctx.ai_run_id is not None:
            db.execute(
                update(AiRun)
                .where(AiRun.business_id == ctx.business_id, AiRun.id == ctx.ai_run_id)
                .values(
                    input_tokens=AiRun.input_tokens + usage.input_tokens,
                    output_tokens=AiRun.output_tokens + usage.output_tokens,
                    cost_micros=AiRun.cost_micros + cost,
                    latency_ms=AiRun.latency_ms + (result.latency_ms if result else 0),
                    provider=result.provider if result else entry.provider,
                    model=result.model if result else entry.model,
                )
            )
        maybe_notify_80_percent(db, ctx)
        team_budget.reconcile_model(db, ctx, r.day, delta_cost)
    team_budget.notify_limit(ctx, day=r.day)


class ReservedAdapter:
    """Wrap only this call's adapters, leaving cached/shared routers unchanged. Fallbacks reserve independently."""

    def __init__(self, ctx: Context, adapter: Adapter, entries: list[RouteEntry]) -> None:
        self.ctx, self.adapter, self.entries = ctx, adapter, entries
        self.name = adapter.name

    def chat(self, req: ChatRequest | TranscriptionRequest) -> ChatResult:
        # Duplicate model entries may have different prices: reserve the more expensive rates conservatively.
        entries = [e for e in self.entries if e.model == req.model]
        entry = entries[0].model_copy(
            update={
                "price_in_per_m": max(e.price_in_per_m for e in entries),
                "price_out_per_m": max(e.price_out_per_m for e in entries),
            }
        )
        with model_slot():
            try:
                r = reserve(self.ctx, entry, req)
            except AiError as exc:
                # The failed reservation was rolled back; notices about an already exhausted budget
                # need their own committed transaction (including zero limits or an upgrade at 100%).
                with transaction(self.ctx) as db:
                    maybe_notify_80_percent(db, self.ctx)
                if exc.code == "ai.agent_limit_reached":
                    team_budget.notify_limit(self.ctx, force=True)
                elif exc.code.startswith("ai.chain_"):
                    with transaction(self.ctx) as db:
                        team_budget.stop_chain(db, self.ctx, exc.code)
                raise
            result = None
            try:
                if isinstance(req, TranscriptionRequest):
                    result = self.adapter.transcribe(req)  # type: ignore[attr-defined]
                else:
                    result = self.adapter.chat(req)
                return result
            finally:
                reconcile(self.ctx, r, entry, result)
