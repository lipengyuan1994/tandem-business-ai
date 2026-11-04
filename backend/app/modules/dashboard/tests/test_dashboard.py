"""M11 dashboard tests (docs/roadmap/modules/M11-dashboard.md#tests-must-exist)."""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.db import transaction
from app.platform.ids import fixture_id
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for


def test_today_sections_respect_permissions(registry: Registry) -> None:
    app = create_app(registry)
    business = make_business()
    owner = login_as(app, business, ["owner"]).get("/api/v1/dashboard/today").json()
    assert (
        owner["agenda"] == []
        and owner["approvals"]["can_decide"] == 0
        and owner["low_stock"] == []
        and owner["delivery_issues"] == 0
        and owner["conversations"] is not None
    )
    stock = login_as(app, business, ["inventory_manager"]).get("/api/v1/dashboard/today").json()
    assert stock["conversations"] is None and stock["low_stock"] == [] and stock["delivery_issues"] is None


def test_today_uses_business_local_day(registry: Registry) -> None:
    # 23:30 on Mon 9 Nov in Denver is already Tue 10 Nov in UTC: "today" must be the local day.
    clock.set_clock(lambda: datetime(2026, 11, 10, 6, 30, tzinfo=UTC))
    app = create_app(registry)
    t = login_as(app, make_business(), ["owner"]).get("/api/v1/dashboard/today").json()
    assert t["local_date"] == "2026-11-09" and t["timezone"] == "America/Denver"


def test_today_summary_matches_today_endpoint(registry: Registry) -> None:
    from app.modules.dashboard.public import today_summary
    from app.seed import seed

    clock.set_clock(lambda: datetime(2026, 11, 10, 16, tzinfo=UTC))
    seed()
    app = create_app(registry)
    for roles in (["owner"], ["inventory_manager"]):
        client = login_as(app, fixture_id("biz-cycle"), roles)
        ctx = ctx_for(fixture_id("biz-cycle"), client.permissions, user_id=client.user_id)
        with transaction(ctx) as db:
            summary = today_summary(db, ctx).model_dump(mode="json")
        assert summary == client.get("/api/v1/dashboard/today").json()


def test_counters_approvals_only_counts_types_user_can_approve(registry: Registry) -> None:
    app = create_app(registry)
    business = make_business()
    operator = login_as(app, business, ["operator"])
    operator.post(
        "/api/v1/approvals/proposals",
        json={"action_type": "testing.noop_write", "title": "x", "payload": {"name": "x"}},
    )
    admin, stock = login_as(app, business, ["admin"]), login_as(app, business, ["inventory_manager"])
    assert admin.get("/api/v1/dashboard/counters").json()["approvals_i_can_decide"] == 1
    assert stock.get("/api/v1/dashboard/counters").json()["approvals_i_can_decide"] == 0


def test_today_is_fast_on_seed_data(registry: Registry) -> None:
    from app.seed import seed

    clock.set_clock(lambda: datetime(2026, 11, 10, 16, 0, tzinfo=UTC))  # the demo day: agenda, approvals, low stock
    seed()
    owner: Any = login_as(create_app(registry), fixture_id("biz-cycle"), ["owner"])
    first = owner.get("/api/v1/dashboard/today").json()
    assert first["agenda"] and first["low_stock"]
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        assert owner.get("/api/v1/dashboard/today").status_code == 200
        timings.append(time.perf_counter() - started)
    median_ms = statistics.median(timings) * 1000
    structlog.get_logger(__name__).info("dashboard_today_timing", median_ms=round(median_ms, 1))
    assert median_ms < 150, f"/dashboard/today median {median_ms:.0f} ms on the seed (target < 150 ms)"
