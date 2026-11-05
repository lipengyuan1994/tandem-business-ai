"""Real PostgreSQL races, failed/fallback attempts, UTC rollover and independently queued notices."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Event
from typing import Any

import pytest
from sqlalchemy import select

from app.modules.ai.agent.runner import ModelCaller, StopRun
from app.modules.ai.budget_models import CostPeriod, PublicAllowance
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import Router
from app.modules.ai.gateway.routes import Limits, ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.gateway.types import ChatMessage, ChatRequest, ChatResult, ProviderError, Usage
from app.modules.ai.models import AiUsageDaily
from app.modules.ai.reservations import ReservedAdapter, conservative_input, model_slot, reconcile, reserve
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.usage import usage_today
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.context import system_context
from app.platform.db import transaction

ENTRY = RouteEntry(provider="fake_agent", model="fake-agent", price_in_per_m=1, price_out_per_m=2)
pytestmark = pytest.mark.usefixtures("registry")


@pytest.fixture
def business() -> uuid.UUID:
    return make_business()


@pytest.fixture
def other_business() -> uuid.UUID:
    return make_business(slug="second-example")


def request(**changes: Any) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content="Can you help?")],
        use_case="summary",
        max_output_tokens=100,
        **changes,
    )


def allowance(req: ChatRequest) -> int:
    return conservative_input(req) + req.max_output_tokens


def test_does_not_fit_refuses_before_provider_and_rolls_back_all_counters(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request()
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", allowance(req) - 1)
    gw = FakeGateway(["unused"])
    with pytest.raises(AiError, match=r"ai\.budget_exceeded"):
        ReservedAdapter(system_context(business), gw, [ENTRY]).chat(req.model_copy(update={"model": ENTRY.model}))
    assert not gw.calls
    with transaction(system_context(business)) as db:
        assert usage_today(db, system_context(business)).input_tokens == 0
        assert list(db.scalars(select(CostPeriod))) == []


@pytest.mark.concurrency
def test_business_budget_reservations_serialize_in_flight_calls(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request()
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", allowance(req) * 2)
    start = Barrier(8)

    def attempt(_: int) -> bool:
        start.wait(timeout=10)
        try:
            reserve(system_context(business), ENTRY, req)
            return True
        except AiError as exc:
            assert exc.code == "ai.budget_exceeded"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 2
    with transaction(system_context(business)) as db:
        row = usage_today(db, system_context(business))
        assert row.input_tokens + row.output_tokens == allowance(req) * 2
        assert row.percent == 100


@pytest.mark.parametrize("setting", ["ai_daily_cost_limit_usd", "ai_monthly_cost_limit_usd"])
@pytest.mark.concurrency
def test_installation_cost_ceiling_is_atomic_across_businesses(
    business: uuid.UUID, other_business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    req = request()
    price = conservative_input(req) + 2 * req.max_output_tokens
    monkeypatch.setattr(get_settings(), setting, Decimal(price * 2) / 1_000_000)
    start = Barrier(6)

    def attempt(i: int) -> bool:
        start.wait(timeout=10)
        try:
            reserve(system_context(business if i % 2 else other_business), ENTRY, req)
            return True
        except AiError as exc:
            assert exc.code == "ai.budget_exceeded"
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(attempt, range(6))) == 2
    with transaction(system_context(business)) as db:
        assert list(db.scalars(select(CostPeriod.cost_micros))) == [price * 2, price * 2]


@pytest.mark.parametrize("failure", [ProviderError("timeout"), ProviderError("bad_request"), RuntimeError("crash")])
def test_failed_attempt_retains_full_reservation(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    class Failing:
        name = "fake_agent"

        def chat(self, req: ChatRequest) -> ChatResult:
            raise failure

    req = request().model_copy(update={"model": ENTRY.model})
    with pytest.raises(type(failure)):
        ReservedAdapter(system_context(business), Failing(), [ENTRY]).chat(req)
    with transaction(system_context(business)) as db:
        row = usage_today(db, system_context(business))
        assert row.input_tokens + row.output_tokens == allowance(req)
        assert row.cost_micros == conservative_input(req) + 2 * req.max_output_tokens
    # A later attempt cannot reuse the allowance charged for the failed one.
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", allowance(req))
    with pytest.raises(AiError, match=r"ai\.budget_exceeded"):
        reserve(system_context(business), ENTRY, req)


@pytest.mark.parametrize("reported", [True, False])
def test_reconcile_real_usage_or_retain_unknown_usage(business: uuid.UUID, reported: bool) -> None:
    ctx, req = system_context(business), request()
    r = reserve(ctx, ENTRY, req)
    result = ChatResult(
        text="",
        usage=Usage(input_tokens=10 if reported else 0, output_tokens=5 if reported else 0),
        provider=ENTRY.provider,
        model=ENTRY.model,
        latency_ms=1,
    )
    reconcile(ctx, r, ENTRY, result)
    with transaction(ctx) as db:
        row = usage_today(db, ctx)
        assert row.input_tokens == (10 if reported else r.input_tokens)
        assert row.output_tokens == (5 if reported else r.output_tokens)
        assert row.cost_micros == (20 if reported else r.cost)
        assert list(db.scalars(select(CostPeriod.cost_micros))) == [row.cost_micros, row.cost_micros]


def test_router_fallback_charges_failed_and_successful_attempts(business: uuid.UUID) -> None:
    failing = FakeGateway([ProviderError("timeout")])
    success = FakeGateway(['{"ok":true}'])
    entries = [ENTRY, ENTRY.model_copy(update={"provider": "fallback"})]
    router = Router(
        RoutesFile(
            providers={p: ProviderCfg(kind="fake", vendor="vultr") for p in (ENTRY.provider, "fallback")},
            routes={"agent": entries},
            limits=Limits(max_output_tokens_per_step=100),
        ),
        {ENTRY.provider: failing, "fallback": success},
    )
    result = ModelCaller(system_context(business), router, "agent", "hosted_vultr", "summary").call(
        request().messages, {"type": "object"}
    )
    assert len(failing.calls) == len(success.calls) == 1
    failed_req = failing.calls[0]
    with transaction(system_context(business)) as db:
        row = usage_today(db, system_context(business))
        assert row.input_tokens == conservative_input(failed_req) + result.usage.input_tokens
        assert row.output_tokens == failed_req.max_output_tokens + result.usage.output_tokens


def test_budget_denies_fallback_attempt_without_calling_it(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = FakeGateway([ProviderError("timeout")]), FakeGateway(["unused"])
    req = request()
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", allowance(req))
    ctx = system_context(business)
    with pytest.raises(ProviderError):
        ReservedAdapter(ctx, first, [ENTRY]).chat(req.model_copy(update={"model": ENTRY.model}))
    with pytest.raises(AiError, match=r"ai\.budget_exceeded"):
        ReservedAdapter(ctx, second, [ENTRY]).chat(req.model_copy(update={"model": ENTRY.model}))
    assert not second.calls


@pytest.mark.concurrency
def test_process_model_concurrency_cap_and_release_on_exception(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "ai_max_concurrent_calls", 1)
    entered, release = Event(), Event()

    def blocking(_: ChatRequest) -> str:
        entered.set()
        assert release.wait(timeout=10)
        raise ProviderError("timeout")

    gw = FakeGateway(blocking)
    adapter = ReservedAdapter(system_context(business), gw, [ENTRY])
    req = request().model_copy(update={"model": ENTRY.model})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(adapter.chat, req)
        assert entered.wait(timeout=10)
        try:
            with pytest.raises(AiError, match=r"ai\.provider_unavailable"):
                adapter.chat(req)
            assert len(gw.calls) == 1
        finally:
            release.set()
        with pytest.raises(ProviderError):
            future.result(timeout=10)
    with model_slot():  # failed call released the process slot
        pass


def test_structured_and_agent_share_the_process_cap(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_max_concurrent_calls", 1)
    gw = FakeGateway(["unused"])
    caller = ModelCaller(system_context(business), fake_router_by_class(agent=gw), "agent", "hosted_vultr", "summary")
    with model_slot(), pytest.raises(StopRun) as error:
        caller.call(request().messages, {})
    assert error.value.error_code == "ai.provider_unavailable" and not gw.calls


def test_reconcile_uses_reserved_utc_day_and_month(business: uuid.UUID, freeze_time: Any) -> None:
    at = datetime(2026, 11, 30, 23, 59, tzinfo=UTC)
    freeze_time(at)
    ctx = system_context(business)
    r = reserve(ctx, ENTRY, request())
    freeze_time(at + timedelta(minutes=2))
    reconcile(
        ctx,
        r,
        ENTRY,
        ChatResult(
            text="ok",
            usage=Usage(input_tokens=10, output_tokens=5),
            provider=ENTRY.provider,
            model=ENTRY.model,
            latency_ms=1,
        ),
    )
    with transaction(ctx) as db:
        rows = list(db.scalars(select(AiUsageDaily)))
        assert len(rows) == 1 and rows[0].day == at.date() and rows[0].input_tokens == 10
        assert list(db.scalars(select(CostPeriod.cost_micros))) == [20, 20]
        assert usage_today(db, ctx).input_tokens == 0


def test_public_token_subbudget_preserves_staff_allowance(business: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    req = request()
    tokens = allowance(req)
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", tokens * 4)
    ctx = system_context(business)
    reserve(ctx, ENTRY, req.model_copy(update={"use_case": "intake"}))
    with pytest.raises(AiError, match=r"ai\.budget_exceeded"):
        reserve(ctx, ENTRY, req.model_copy(update={"use_case": "safety"}))
    for _ in range(3):
        reserve(ctx, ENTRY, req)  # staff summary/copilot has the other 75%
    with transaction(ctx) as db:
        assert db.scalar(select(PublicAllowance.used)) == tokens
        assert usage_today(db, ctx).percent == 100


def test_model_caller_preserves_decorated_router_behavior(business: uuid.UUID) -> None:
    class Guard(Router):
        def __init__(self, delegate: Router) -> None:
            self.delegate = delegate
            self.limits = delegate.limits
            self.calls = 0

        def allowed(self, model_class: Any, policy: Any) -> Any:
            return self.delegate.allowed(model_class, policy)

        def complete(self, model_class: Any, policy: Any, req: ChatRequest) -> Any:
            self.calls += 1
            return self.delegate.complete(model_class, policy, req)

    gw = FakeGateway(['{"ok":true}'])
    guarded = Guard(fake_router_by_class(agent=gw))
    caller = ModelCaller(system_context(business), guarded, "agent", "hosted_vultr", "summary")
    caller.call(request().messages, {})
    assert guarded.calls == 1 and len(gw.calls) == 1
    with transaction(system_context(business)) as db:
        assert usage_today(db, system_context(business)).input_tokens > 0
