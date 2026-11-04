"""P1-03: ids, canonical JSON, problem details, clock, money, cursors."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform.canonical import canonical_json, sha256_hex
from app.platform.clock import local_day_bounds
from app.platform.errors import Conflict, install_error_handlers
from app.platform.ids import fixture_id, new_id
from app.platform.logging import RequestIdMiddleware
from app.platform.money import CurrencyMismatch, Money
from app.platform.pagination import decode_cursor, encode_cursor


def test_uuid7_sortable_and_versioned() -> None:
    first = new_id()
    time.sleep(0.002)
    second = new_id()
    assert first.version == 7 and second.version == 7
    assert first.variant == "specified in RFC 4122"
    assert str(first) < str(second)
    assert fixture_id("cust-ada") == fixture_id("cust-ada") != fixture_id("cust-ben")


def test_canonical_json_stable_for_key_order_and_decimals() -> None:
    a = {"b": Decimal("1.50"), "a": [1, {"y": 2, "x": UUID(int=1)}], "t": datetime(2026, 11, 10, 17, 0, tzinfo=UTC)}
    b = {
        "t": datetime(2026, 11, 10, 10, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/Denver")),
        "a": [1, {"x": UUID(int=1), "y": 2}],
        "b": Decimal("1.5"),
    }
    assert canonical_json(a) == canonical_json(b)
    assert sha256_hex(a) == sha256_hex(b)
    with pytest.raises(ValueError):
        canonical_json({"t": datetime(2026, 1, 1)})


def test_problem_details_shape() -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise Conflict("crm.customer_version_conflict", current_version=3)

    response = TestClient(app).get("/boom")
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "crm.customer_version_conflict"
    assert body["type"].endswith("/crm.customer_version_conflict")
    assert body["current_version"] == 3
    assert body["request_id"] == response.headers["x-request-id"]


def test_local_day_bounds_dst() -> None:
    start, end = local_day_bounds(date(2026, 11, 1), "America/Denver")  # DST ends: 25-hour day
    assert (end - start).total_seconds() == 25 * 3600
    start, end = local_day_bounds(date(2026, 3, 8), "America/Denver")  # DST starts: 23-hour day
    assert (end - start).total_seconds() == 23 * 3600


def test_money_rejects_currency_mismatch() -> None:
    assert Money(500, "USD") + Money(250, "USD") == Money(750, "USD")
    with pytest.raises(CurrencyMismatch):
        Money(1, "USD") + Money(1, "GBP")
    with pytest.raises(ValueError):
        Money(1, "usd")


def test_cursor_roundtrip() -> None:
    ts, rid = datetime(2026, 11, 5, 17, 30, tzinfo=UTC), new_id()
    assert decode_cursor(encode_cursor(ts, rid)) == (ts, rid)
