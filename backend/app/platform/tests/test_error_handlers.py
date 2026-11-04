"""Problem+json details clients rely on: `Retry-After` on every 429 and plain field paths on public routes.

Security re-scan 2026-11-03, findings F6 and F7 (docs/security/2026-11-03-rescan.md).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.platform import ratelimit
from app.platform.db import transaction
from app.platform.errors import Conflict, RateLimited, install_error_handlers
from app.platform.logging import RequestIdMiddleware
from app.platform.testing.auth import ctx_for

PUBLIC = "/api/v1/public/"


# ------------------------------------------------------------------------------------------------ Retry-After (F7)
def _app(**kwargs: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app, **kwargs)
    return app


def test_rate_limited_answers_carry_retry_after_in_whole_seconds() -> None:
    app = _app()

    @app.get("/slow-down")
    def slow_down() -> None:
        raise RateLimited("platform.rate_limited", retry_after_seconds=37)

    response = TestClient(app).get("/slow-down")
    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["retry-after"] == "37"
    body = response.json()
    assert body["code"] == "platform.rate_limited" and body["retry_after_seconds"] == 37
    assert body["request_id"] == response.headers["x-request-id"]


def test_retry_after_is_never_zero_and_other_errors_have_none() -> None:
    app = _app()

    @app.get("/edge")
    def edge() -> None:
        raise RateLimited("platform.rate_limited", retry_after_seconds=0)

    @app.get("/conflict")
    def conflict() -> None:
        raise Conflict("crm.customer_version_conflict")

    client = TestClient(app)
    assert client.get("/edge").headers["retry-after"] == "1"  # "retry now" would invite a tight loop
    assert "retry-after" not in client.get("/conflict").headers


def test_retry_after_is_what_is_left_of_the_limiters_window(business: UUID, freeze_time: Any) -> None:
    """The real limiter, through a route: 5 s into a 60 s window leaves 55 s; the next window starts afresh."""
    app = _app()

    @app.get("/limited")
    def limited() -> dict[str, str]:
        with transaction(ctx_for(business)) as db:
            ratelimit.hit(db, "retry-after-test", limit=2, window_seconds=60)
        return {"ok": "yes"}

    client = TestClient(app)
    freeze_time(datetime(2026, 11, 5, 17, 0, 5, tzinfo=UTC))
    assert [client.get("/limited").status_code for _ in range(2)] == [200, 200]
    refused = client.get("/limited")
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "55" == str(refused.json()["retry_after_seconds"])
    freeze_time(datetime(2026, 11, 5, 17, 0, 59, tzinfo=UTC))
    assert client.get("/limited").headers["retry-after"] == "1"  # the last second of the window
    freeze_time(datetime(2026, 11, 5, 17, 1, 0, tzinfo=UTC))
    assert client.get("/limited").status_code == 200


# ------------------------------------------------------------------------------------- plain field paths (F6)
# The shape of the public quote POST: a union of a class-named member and a member wrapped in a validator, so that
# Pydantic names them `Decline` and `function-after[no_duplicates(), Quote]` in the error location.
class Decline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    declined: Literal[True]


class QuoteLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: UUID
    unit_price_minor: int = Field(ge=0)


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[QuoteLine] = Field(min_length=1)
    lead_time_days: int = Field(ge=0)
    declined: Literal[False] = False

    @model_validator(mode="after")
    def no_duplicates(self) -> Quote:
        if len({line.item_id for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate item")
        return self


class Message(BaseModel):
    body: str = Field(min_length=1)


def _union_app() -> FastAPI:
    app = _app(public_prefix=PUBLIC)

    @app.post(PUBLIC + "quotes/{token}")
    def public_quote(token: str, body: Decline | Quote) -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/purchasing/quotes")
    def staff_quote(body: Decline | Quote) -> dict[str, str]:
        return {"status": "ok"}

    @app.post(PUBLIC + "chat")
    def public_chat(body: Message) -> dict[str, str]:
        return {"status": "ok"}

    @app.get(PUBLIC + "things")
    def public_list(cursor: int = 0) -> dict[str, int]:
        return {"cursor": cursor}

    return app


ITEM = "6f7a0a4c-0000-4000-8000-000000000001"
INVALID_BODIES = [
    {},
    {"declined": True, "unexpected": 1},
    {"lines": [{"item_id": "nope", "unit_price_minor": -1}], "lead_time_days": "x", "declined": False},
    {
        "lines": [{"item_id": ITEM, "unit_price_minor": 5}, {"item_id": ITEM, "unit_price_minor": 5}],
        "lead_time_days": 3,
    },
]


PLAIN_PATH = re.compile(r"[a-z][a-z0-9_]*(\.([a-z][a-z0-9_]*|\d+))*")


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_public_validation_errors_name_fields_not_internal_classes(body: dict[str, Any]) -> None:
    response = TestClient(_union_app()).post(PUBLIC + "quotes/tok", json=body)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors
    for error in errors:
        # JSON field names and list positions only (or "" for a rule about the whole body): no class or function names.
        assert error["field"] == "" or PLAIN_PATH.fullmatch(error["field"]), error
    assert len({(e["field"], e["code"]) for e in errors}) == len(errors)  # once the names are gone, no repeats


def test_public_validation_errors_keep_the_paths_a_form_can_map() -> None:
    errors = TestClient(_union_app()).post(PUBLIC + "quotes/tok", json=INVALID_BODIES[2]).json()["errors"]
    fields = {e["field"]: e["code"] for e in errors}
    assert fields["lines.0.unit_price_minor"] == "greater_than_equal"
    assert fields["lines.0.item_id"] == "uuid_parsing" and fields["lead_time_days"] == "int_parsing"
    # whole-body rule (duplicate items): no field to point at
    errors = TestClient(_union_app()).post(PUBLIC + "quotes/tok", json=INVALID_BODIES[3]).json()["errors"]
    assert {"field": "", "code": "value_error"} in errors


def test_staff_validation_errors_keep_the_full_path() -> None:
    response = TestClient(_union_app()).post("/api/v1/purchasing/quotes", json=INVALID_BODIES[2])
    fields = [e["field"] for e in response.json()["errors"]]
    assert "Decline.lines" in fields
    assert "function-after[no_duplicates(), Quote].lines.0.unit_price_minor" in fields


def test_a_public_field_called_body_is_reported_and_query_parameters_keep_their_names() -> None:
    client = TestClient(_union_app())
    assert client.post(PUBLIC + "chat", json={"body": ""}).json()["errors"] == [
        {"field": "body", "code": "string_too_short"}
    ]
    assert client.get(PUBLIC + "things", params={"cursor": "abc"}).json()["errors"] == [
        {"field": "cursor", "code": "int_parsing"}
    ]


def test_without_a_public_prefix_nothing_changes() -> None:
    """`install_error_handlers(app)` as other tests and tools call it: the staff format everywhere."""
    app = _app()

    @app.post("/anything")
    def anything(body: Decline | Quote) -> None:
        return None

    errors = TestClient(app).post("/anything", json={}).json()["errors"]
    assert next(e["field"] for e in errors) == "Decline.declined"
