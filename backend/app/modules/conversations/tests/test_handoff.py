from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.conversations import public as conversations
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.context import system_context
from app.platform.db import transaction

CHAT = "/api/v1/public/example-cycle-works/chat"


@pytest.fixture
def setup() -> tuple[Any, uuid.UUID]:
    biz = make_business()
    return create_app(), biz


@pytest.mark.parametrize("assigned", [False, True])
def test_handoff_notifies_assigned_or_eligible_staff_and_is_idempotent(
    setup: tuple[Any, uuid.UUID], owner_engine: Engine, assigned: bool
) -> None:
    app, biz = setup
    a = login_as(app, biz, ["operator"])
    b = login_as(app, biz, ["owner"])
    login_as(app, biz, ["auditor"])
    v = TestClient(app, base_url="https://testserver")
    start = v.post(CHAT, json={"name": "Ada Example", "message": "Warranty?"})
    c = start.json()["conversation_id"]
    assert "AI assistant" in start.json()["ai_greeting"]
    assert "AI assistant" in v.get("/api/v1/public/example-cycle-works/profile").json()["ai_greeting"]
    if assigned:
        with owner_engine.begin() as db:
            db.execute(text("update conversations set assigned_to=:u where id=:c"), {"u": a.user_id, "c": c})
    for _ in range(2):
        response = v.post(CHAT + "/handoff")
        assert response.status_code == 200, response.text
    thread = a.get(f"/api/v1/conversations/{c}").json()
    assert thread["ai_mode"] == "human" and thread["ai_paused_at"]
    with owner_engine.connect() as db:
        users = set(db.scalars(text("select user_id from in_app_notifications where kind='handoff'")))
        assert users == ({a.user_id} if assigned else {a.user_id, b.user_id})
        assert (
            db.scalar(text("select count(*) from messages where conversation_id=:c and author_kind='system'"), {"c": c})
            == 1
        )
    assert a.post(f"/api/v1/conversations/{c}/hand-back").status_code == 200
    assert a.get(f"/api/v1/conversations/{c}").json()["ai_mode"] == "auto"
    assert a.post(f"/api/v1/conversations/{c}/take-over").status_code == 200
    assert a.get(f"/api/v1/conversations/{c}").json()["ai_mode"] == "human"


@pytest.mark.parametrize(
    "hour,expected", [(10, "A person will reply here."), (23, "Our team will reply after opening.")]
)
def test_handoff_business_local_hours_and_configurable_text(
    setup: tuple[Any, uuid.UUID], owner_engine: Engine, hour: int, expected: str
) -> None:
    app, biz = setup
    clock.set_clock(lambda: datetime(2026, 11, 5, hour, 0, tzinfo=UTC))
    with owner_engine.begin() as db:
        db.execute(text("update businesses set timezone='UTC' where id=:b"), {"b": biz})
        db.execute(
            text(
                "update business_settings set settings=settings || jsonb_build_object("
                "'handoff_after_hours_text','Our team will reply after opening.') where business_id=:b"
            ),
            {"b": biz},
        )
        db.execute(
            text(
                "insert into opening_hours (id,business_id,location_id,weekday,opens,closes) "
                "select gen_random_uuid(),:b,id,3,'09:00','17:00' from locations where business_id=:b"
            ),
            {"b": biz},
        )
    v = TestClient(app, base_url="https://testserver")
    c = v.post(CHAT, json={"name": "Ada Example", "message": "Help"}).json()["conversation_id"]
    response = v.post(CHAT + "/handoff")
    assert response.status_code == 200, response.text
    assert response.json()["messages"][-1]["body"] == expected
    assert response.json()["messages"][-1]["is_system"] is True
    assert response.json()["messages"][0]["is_system"] is False
    ctx = system_context(biz, frozenset({"conversations.thread.read"}))
    with transaction(ctx) as db:
        assert conversations.get_thread(db, ctx, uuid.UUID(c)).status == "waiting_on_staff"


def test_staff_reply_takes_over_handoff_requires_token_and_is_rate_limited(setup: tuple[Any, uuid.UUID]) -> None:
    app, biz = setup
    staff = login_as(app, biz, ["operator"])
    visitor = TestClient(app, base_url="https://testserver")
    assert visitor.post(CHAT + "/handoff").status_code == 403
    c = visitor.post(CHAT, json={"name": "Ada Example", "message": "Hello"}).json()["conversation_id"]
    response = staff.post(f"/api/v1/conversations/{c}/messages", json={"direction": "outbound", "body": "I can help."})
    assert response.status_code == 201, response.text
    assert staff.get(f"/api/v1/conversations/{c}").json()["ai_mode"] == "human"
    assert staff.post(f"/api/v1/conversations/{c}/hand-back").status_code == 200
    for _ in range(10):
        assert visitor.post(CHAT + "/handoff").status_code == 200
    assert visitor.post(CHAT + "/handoff").status_code == 429
