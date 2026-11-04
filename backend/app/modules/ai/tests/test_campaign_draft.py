"""AI-15 campaign API: assistant revisions only; no audience or delivery."""

import json
import uuid

import pytest
from sqlalchemy import text

from app.main import create_app
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.org.testing import make_business
from app.platform.testing.auth import client_for

PERMISSIONS = {"ai.assistant.use", "marketing.campaign.read", "marketing.campaign.write", "org.business.read"}
GOOD = {"subject": "A friendly hello", "body_markdown": "Hello from the shop! We look forward to seeing you again."}


@pytest.fixture(autouse=True)
def clean_router():
    reset_router_cache()
    yield
    reset_router_cache()


def campaign(registry):
    biz = make_business()
    client = client_for(create_app(registry), registry, biz, PERMISSIONS)
    segment = client.post(
        "/api/v1/marketing/segments",
        json={"name": "Road riders", "definition": {"all": [{"field": "tag", "op": "has", "value": "road-bike"}]}},
    )
    assert segment.status_code == 201, segment.text
    response = client.post(
        "/api/v1/marketing/campaigns",
        json={
            "subject": "Staff draft",
            "body_markdown": "Initial staff draft",
            "name": "Welcome back",
            "purpose": "Invite road riders back to the shop",
            "segment_id": segment.json()["id"],
        },
    )
    assert response.status_code == 201, response.text
    return biz, client, response.json()["id"]


def test_campaign_draft_adds_assistant_revision(registry, owner_engine):
    biz, client, cid = campaign(registry)
    gw = FakeGateway([json.dumps(GOOD)])
    set_router_for_tests(fake_router_by_class(writer=gw))
    response = client.post(f"/api/v1/ai/campaigns/{cid}/draft", json={"purpose": "Say hello to road riders"})
    assert response.status_code == 200, response.text
    out = response.json()
    assert set(out) == {"run_id", "revision_id"}
    uuid.UUID(out["run_id"])
    with owner_engine.connect() as conn:
        revision = conn.execute(text("select * from campaign_revisions where id=:id"), {"id": out["revision_id"]}).one()
        assert revision.created_by_kind == "assistant" and str(revision.ai_run_id) == out["run_id"]
        assert revision.subject == GOOD["subject"] and revision.body_markdown == GOOD["body_markdown"]
        assert conn.execute(text("select status from campaigns where id=:id"), {"id": cid}).scalar_one() == "draft"
        assert (
            conn.execute(text("select count(*) from audience_snapshots where business_id=:b"), {"b": biz}).scalar_one()
            == 0
        )
    facts = gw.calls[0].messages[1].content
    assert "Say hello to road riders" in facts and "road-bike" in facts and "Road riders" in facts
    assert "<untrusted" in facts


@pytest.mark.parametrize(
    "bad",
    [
        "Visit https://example.com",
        "Visit www.example.com",
        "Visit example.com/deal",
        "UNSUBSCRIBE here",
        "[link](//example.com)",
        "<https://example.com>",
        "Contact mailto:hello@example.com",
        "See [our offer](/offers)",
        "Visit https%3A%2F%2Fexample.com",
        "UNSUB&#83;CRIBE here",
        "Visit 127.0.0.1:8000/offers",
        "   ",
    ],
)
def test_campaign_draft_rejects_urls_and_own_unsubscribe_text(registry, owner_engine, bad):
    biz, client, cid = campaign(registry)
    gw = FakeGateway([json.dumps({**GOOD, "body_markdown": bad})] * 2)
    set_router_for_tests(fake_router_by_class(writer=gw))
    response = client.post(f"/api/v1/ai/campaigns/{cid}/draft", json={})
    assert response.status_code == 502 and response.json()["code"] == "ai.invalid_model_output", response.text
    assert len(gw.calls) == 2
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from campaign_revisions where business_id=:b and created_by_kind='assistant'"),
                {"b": biz},
            ).scalar_one()
            == 0
        )


def test_campaign_draft_repairs_subject_url(registry):
    _, client, cid = campaign(registry)
    gw = FakeGateway([json.dumps({**GOOD, "subject": "Visit example.com"}), json.dumps(GOOD)])
    set_router_for_tests(fake_router_by_class(writer=gw))
    response = client.post(f"/api/v1/ai/campaigns/{cid}/draft", json={})
    assert response.status_code == 200, response.text
    assert len(gw.calls) == 2


def test_campaign_draft_uses_no_customer_data(registry, monkeypatch):
    from app.modules.marketing import public as marketing

    biz, client, cid = campaign(registry)
    crm_client = client_for(create_app(registry), registry, biz, {"crm.customer.write", "crm.customer.read"})
    customer = crm_client.post(
        "/api/v1/crm/customers",
        json={
            "display_name": "Ada Customer Canary",
            "tags": ["road-bike"],
            "contact_points": [{"type": "email", "value": "ada.private@example.com"}],
        },
    )
    assert customer.status_code == 201, customer.text

    def forbidden(*args, **kwargs):
        raise AssertionError("Audience data must never be read by the AI draft")

    monkeypatch.setattr(marketing, "preview_audience", forbidden)
    gw = FakeGateway([json.dumps(GOOD)])
    set_router_for_tests(fake_router_by_class(writer=gw))
    response = client.post(f"/api/v1/ai/campaigns/{cid}/draft", json={})
    assert response.status_code == 200, response.text
    facts = gw.calls[0].messages[1].content
    assert "customer_id" not in facts and "members" not in facts
    assert "Ada Customer Canary" not in facts and "ada.private@example.com" not in facts
    assert customer.json()["id"] not in facts


def test_campaign_draft_accepts_write_and_ai_permissions_without_body(registry):
    biz, _, cid = campaign(registry)
    gw = FakeGateway([json.dumps(GOOD)])
    set_router_for_tests(fake_router_by_class(writer=gw))
    client = client_for(create_app(registry), registry, biz, {"marketing.campaign.write", "ai.assistant.use"})
    response = client.post(f"/api/v1/ai/campaigns/{cid}/draft")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"run_id", "revision_id"}
    assert "Invite road riders back to the shop" in gw.calls[0].messages[1].content


@pytest.mark.parametrize("perms", [{"ai.assistant.use"}, {"marketing.campaign.write"}, set()])
def test_campaign_draft_requires_permissions(registry, perms):
    biz, _, cid = campaign(registry)
    gw = FakeGateway([])
    set_router_for_tests(fake_router_by_class(writer=gw))
    client = client_for(create_app(registry), registry, biz, perms)
    response = client.post(f"/api/v1/ai/campaigns/{cid}/draft", json={})
    assert response.status_code == 403 and not gw.calls


def test_campaign_draft_cannot_read_another_business(registry):
    _, _, cid = campaign(registry)
    biz = make_business(slug="other")
    gw = FakeGateway([])
    set_router_for_tests(fake_router_by_class(writer=gw))
    response = client_for(create_app(registry), registry, biz, PERMISSIONS).post(
        f"/api/v1/ai/campaigns/{cid}/draft", json={}
    )
    assert response.status_code == 404 and not gw.calls
