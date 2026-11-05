"""Budget notices use the existing outbox; invalid limits fail closed at startup."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.ai.reservations import conservative_input, reserve
from app.modules.ai.tests.test_batch_g_budgets import ENTRY, allowance, request
from app.modules.ai.usage import maybe_notify_80_percent
from app.modules.identity.testing import add_member, make_user
from app.modules.org.testing import make_business
from app.platform.config import Settings, get_settings
from app.platform.context import system_context
from app.platform.db import transaction

pytestmark = pytest.mark.usefixtures("registry")


@pytest.fixture
def business() -> uuid.UUID:
    return make_business()


def test_80_and_100_notices_once_with_operator_outbox(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine
) -> None:
    user = make_user()
    add_member(user, business, ["owner"])
    monkeypatch.setattr(get_settings(), "ai_budget_notification_email", "operator@example.com")
    req = request().model_copy(update={"max_output_tokens": 10_000})
    tokens = allowance(req)
    budget = tokens * 5 // 4
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", budget)
    ctx = system_context(business)
    free = ENTRY.model_copy(update={"price_in_per_m": 0, "price_out_per_m": 0})
    reserve(ctx, free, req)
    remainder = budget - tokens
    reserve(ctx, free, req.model_copy(update={"max_output_tokens": remainder - conservative_input(req)}))
    with transaction(ctx) as db:
        maybe_notify_80_percent(db, ctx)
    with owner_engine.connect() as db:
        assert list(db.execute(text("select user_id, kind from in_app_notifications order by kind"))) == [
            (user, "ai.budget_100"),
            (user, "ai.budget_80"),
        ]
        messages = db.execute(text("select recipient, channel, variables from outbox_messages")).all()
        assert len(messages) == 2
        assert all(m.recipient == "operator@example.com" and m.channel == "simulated" for m in messages)
        assert all("budget" in m.variables["body"] for m in messages)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("ai_daily_cost_limit_usd", "-1"),
        ("ai_monthly_cost_limit_usd", "NaN"),
        ("ai_max_concurrent_calls", 0),
        ("ai_public_intake_per_hour", -1),
        ("ai_public_intake_max_pending", -1),
        ("ai_budget_notification_email", "invalid"),
    ],
)
def test_invalid_budget_settings_fail_closed(setting: str, value: Any) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{setting: value})


def test_installation_thresholds_queue_independent_operator_notices(
    business: uuid.UUID, monkeypatch: pytest.MonkeyPatch, owner_engine: Engine
) -> None:
    ctx = system_context(business)
    req = request().model_copy(update={"max_output_tokens": 10_000})
    cost = allowance(req)
    limit = cost * 5 // 4
    monkeypatch.setattr(get_settings(), "ai_daily_cost_limit_usd", Decimal(limit) / 1_000_000)
    monkeypatch.setattr(get_settings(), "ai_monthly_cost_limit_usd", Decimal(limit) / 1_000_000)
    monkeypatch.setattr(get_settings(), "ai_budget_notification_email", "operator@example.com")
    monkeypatch.setattr(get_settings(), "smtp_url", "smtps://user:password@smtp.example.com:465")
    entry = ENTRY.model_copy(update={"price_out_per_m": 1})
    reserve(ctx, entry, req)
    reserve(ctx, entry, req.model_copy(update={"max_output_tokens": limit - cost - conservative_input(req)}))
    with transaction(ctx) as db:
        maybe_notify_80_percent(db, ctx)
    with owner_engine.connect() as db:
        outbox = db.execute(text("select channel, variables from outbox_messages")).all()
        assert len(outbox) == 4 and all(row.channel == "email" for row in outbox)
        bodies = [row.variables["body"] for row in outbox]
        for scope in ("installation_day", "installation_month"):
            for threshold in (80, 100):
                assert sum(f"{scope} budget: {threshold}%" in b for b in bodies) == 1
