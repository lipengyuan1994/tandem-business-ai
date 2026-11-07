"""Emergency backstop, classifier path, queue and synthetic profile boundary."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.main import create_app
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.usecases import intake
from app.modules.conversations import service as messages
from app.modules.conversations.emergency import signals_emergency
from app.modules.conversations.models import Conversation, Message
from app.modules.notifications.models import InAppNotification
from app.modules.org.demo import SYNTHETIC_DEMO_BANNER
from app.modules.scheduling.tests.test_scheduling import shop as shop
from app.platform.config import get_settings
from app.platform.db import transaction
from app.seed import seed


def enable(shop, enabled=True):
    response = shop.owner.get("/api/v1/org/settings").json()
    response.pop("synthetic_demo_banner", None)
    response["settings"].update(
        emergency_handling=enabled, emergency_instructions="Call the clinic at +1 312 555 0100. Do not wait here."
    )
    saved = shop.owner.patch("/api/v1/org/settings", json=response)
    assert saved.status_code == 200, saved.text


@pytest.mark.parametrize(
    "locale,body",
    [
        ("en-US", "severe pain"),
        ("en-US", "not an emergency"),
        ("es-MX", "hinchazón"),
        ("zh-CN", "牙痛"),
        ("fr-FR", "urgent help"),
    ],
)
def test_emergency_keywords_conservative(locale, body):
    assert signals_emergency(body, locale)


def test_rough_filling_is_normal_boundary():
    assert not signals_emergency("My filling feels a bit rough. Can I book a normal visit?", "en-US")


def test_emergency_keyword_is_immediate_idempotent_and_highest_priority(shop):
    enable(shop)
    conv, mid = inbound(shop.business, body="My tooth was knocked out. Book me tomorrow.")
    with transaction(shop.ctx()) as db:
        row = db.get(Conversation, conv)
        assert row.emergency and row.ai_mode == "human" and row.status == "waiting_on_staff"
        outbound = list(
            db.scalars(select(Message).where(Message.conversation_id == conv, Message.direction == "outbound"))
        )
        assert len(outbound) == 1 and outbound[0].author_kind == "system"
        assert (
            "Call the clinic" in outbound[0].body
            and "If this is life-threatening call your local emergency number." in outbound[0].body
        )
        assert list(db.scalars(select(InAppNotification)))
        messages.visitor_message(db, shop.ctx(), row, "This is still an emergency")
        assert (
            len(
                list(
                    db.scalars(select(Message).where(Message.conversation_id == conv, Message.direction == "outbound"))
                )
            )
            == 1
        )
    gateway = FakeGateway([step_final()])
    result = intake.run(mid, shop.business, router=fake_router_by_class(agent=gateway))
    assert not gateway.calls and not result.proposal_ids
    queue = shop.owner.get("/api/v1/ai/work-queue").json()
    assert queue["items"][0]["kind"] == "emergency_handoff" and queue["items"][0]["priority"] == "urgent"


@pytest.mark.parametrize("channel", ["web_chat", "email"])
def test_classifier_emergency_never_calls_booking_agent(shop, channel):
    enable(shop)
    conv, mid = inbound(shop.business, body="Something feels terribly wrong. Please help now.", channel=channel)
    agent = FakeGateway([step_final()])
    safety = FakeGateway([json.dumps({"flagged": False, "category": "none", "emergency": True})])
    result = intake.run(mid, shop.business, router=fake_router_by_class(safety=safety, agent=agent))
    assert len(safety.calls) == 1
    assert not agent.calls and not result.proposal_ids and result.extra["handoff_requested"]
    with transaction(shop.ctx()) as db:
        assert db.get(Conversation, conv).emergency
        assert db.scalar(text("select count(*) from appointments")) == 0


def test_disabled_emergency_rules_use_normal_intake(shop):
    enable(shop, False)
    conv, mid = inbound(shop.business, body="This is urgent, can I arrange an appointment?")
    steps = iter([step_tool("propose_reply", conversation_id=conv, text="Which day would suit you?"), step_final()])
    gateway = FakeGateway(lambda request: next(steps))
    result = intake.run(mid, shop.business, router=fake_router_by_class(agent=gateway))
    assert result.proposal_ids
    with transaction(shop.ctx()) as db:
        assert db.get(Conversation, conv).ai_mode == "auto"


def test_regulated_refuses_marker_at_startup_and_seed_before_reset(shop, monkeypatch, owner_engine):
    with owner_engine.begin() as conn:
        conn.execute(text("update businesses set synthetic_demo=true where id=:id"), {"id": shop.business})
    monkeypatch.setenv("DATA_PROFILE", "regulated")
    monkeypatch.setenv("REGULATED_HOST_BAA", "example, 2026-11-06")
    monkeypatch.setenv("REGULATED_BACKUP_BAA", "example, 2026-11-06")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="refuses synthetic demo"):
            seed(do_reset=True)
        with pytest.raises(RuntimeError, match="refuses synthetic demo"):
            with TestClient(create_app()):
                pass
        with owner_engine.connect() as conn:
            assert conn.scalar(text("select count(*) from businesses where id=:id"), {"id": shop.business}) == 1
    finally:
        get_settings.cache_clear()


def test_banner_backend_is_exact_and_marker_cannot_be_set_by_api(shop, owner_engine):
    response = shop.owner.get("/api/v1/org/business").json()
    rejected = shop.owner.patch("/api/v1/org/business", json={"version": response["version"], "synthetic_demo": True})
    assert rejected.status_code == 422
    with owner_engine.begin() as conn:
        conn.execute(text("update businesses set synthetic_demo=true where id=:id"), {"id": shop.business})
    assert shop.owner.get("/api/v1/org/business").json()["synthetic_demo_banner"] == SYNTHETIC_DEMO_BANNER
    profile = TestClient(shop.app).get("/api/v1/public/example-cycle-works/profile").json()
    assert profile["synthetic_demo_banner"] == SYNTHETIC_DEMO_BANNER


def test_emergency_enabled_without_classifier_fails_closed(shop):
    enable(shop)
    conv, mid = inbound(shop.business, body="Could I arrange a visit?")
    agent = FakeGateway([step_final()])
    result = intake.run(mid, shop.business, router=fake_router_by_class(agent=agent))
    assert result.extra["handoff_requested"] and not agent.calls
    with transaction(shop.ctx()) as db:
        assert db.get(Conversation, conv).emergency


def test_emergency_queue_precedes_newer_work_and_paginates(shop, monkeypatch):
    import uuid
    from datetime import timedelta

    from app.modules.ai import work_queue_api
    from app.platform import clock

    enable(shop)
    conv, _ = inbound(shop.business, body="Severe pain, please help.")
    normal = {
        "id": uuid.uuid4(),
        "kind": "proposal",
        "title": "Newer routine proposal",
        "created_at": clock.now() + timedelta(days=1),
        "status": "pending_review",
    }
    monkeypatch.setattr(work_queue_api.approvals, "proposal_queue_items", lambda *args, **kwargs: [normal])
    first = shop.owner.get("/api/v1/ai/work-queue", params={"limit": 1}).json()
    assert first["items"][0]["conversation_id"] == str(conv)
    second = shop.owner.get("/api/v1/ai/work-queue", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert second["items"][0]["title"] == "Newer routine proposal"
