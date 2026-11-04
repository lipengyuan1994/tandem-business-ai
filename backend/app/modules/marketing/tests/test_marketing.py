"""M12 marketing tests (docs/roadmap/modules/M12-marketing.md#tests-must-exist)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock, events, jobs
from app.platform.config import get_settings
from app.platform.ids import fixture_id
from app.platform.registry import Registry

NOW = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)


class Shop:
    def __init__(self, app: Any, business: uuid.UUID, registry: Registry) -> None:
        self.app, self.business, self.registry = app, business, registry
        self.marketer = login_as(app, business, ["marketing_manager"])
        self.admin = login_as(app, business, ["admin"])
        self.owner = login_as(app, business, ["owner"])

    def customer(
        self, name: str, email: str | None, consent: str | None, tags: tuple[str, ...] = ("road-bike",)
    ) -> str:
        body: dict[str, Any] = {"display_name": name, "tags": list(tags)}
        if email:
            body["contact_points"] = [{"type": "email", "value": email}]
        if consent:
            body["consents"] = [
                {
                    "channel": "email",
                    "purpose": "marketing",
                    "status": consent,
                    "source": "form",
                    "evidence": "web form",
                }
            ]
        r = self.owner.post("/api/v1/crm/customers", json=body)
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def segment(self, tag: str = "road-bike") -> str:
        r = self.marketer.post(
            "/api/v1/marketing/segments",
            json={"name": f"Tag {tag}", "definition": {"all": [{"field": "tag", "op": "has", "value": tag}]}},
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def campaign(self, segment_id: str, subject: str = "Spring service offer") -> dict[str, Any]:
        r = self.marketer.post(
            "/api/v1/marketing/campaigns",
            json={
                "name": "Spring offer",
                "segment_id": segment_id,
                "subject": subject,
                "body_markdown": "10% off a tune-up.",
            },
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    def preview(self, segment_id: str) -> dict[str, Any]:
        r = self.marketer.get(f"/api/v1/marketing/segments/{segment_id}/preview")
        assert r.status_code == 200, r.text
        return dict(r.json())

    def submit(self, campaign_id: str) -> dict[str, Any]:
        r = self.marketer.post(f"/api/v1/marketing/campaigns/{campaign_id}/submit")
        assert r.status_code == 200, r.text
        return dict(r.json())

    def approve(self, proposal_id: str) -> Any:
        p = self.admin.get(f"/api/v1/approvals/proposals/{proposal_id}").json()
        rev = p["current_revision"]
        return self.admin.post(
            f"/api/v1/approvals/proposals/{proposal_id}/decisions",
            json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
        )

    def run_worker(self) -> None:
        for _ in range(3):
            jobs.run_pending(self.registry)
            events.deliver_pending(self.registry)


@pytest.fixture
def shop(registry: Registry) -> Shop:
    clock.set_clock(lambda: NOW)
    return Shop(create_app(registry), make_business("example-cycle-works"), registry)


def test_customer_without_explicit_marketing_consent_excluded(shop: Shop) -> None:
    shop.customer("Ada Example", "ada@example.com", "granted")
    shop.customer("Ben Example", "ben@example.com", None)  # never asked
    shop.customer("Chloe Example", "chloe@example.com", "denied")
    shop.customer("Dana Example", "dana@example.com", "withdrawn")
    preview = shop.preview(shop.segment())
    assert preview["matched"] == 4 and preview["eligible_count"] == 1
    assert preview["excluded_counts"] == {"no_consent": 3, "suppressed": 0, "no_contact": 0, "archived": 0}
    assert [m["display_name"] for m in preview["sample"]] == ["Ada Example"]


def test_exclusion_reasons_no_contact_and_archived(shop: Shop) -> None:
    shop.customer("Ada Example", "ada@example.com", "granted")
    shop.customer("Phone Only", None, "granted")
    gone = shop.customer("Gone Example", "gone@example.com", "granted")
    detail = shop.owner.get(f"/api/v1/crm/customers/{gone}").json()
    shop.owner.post(f"/api/v1/crm/customers/{gone}/archive", json={"version": detail["version"]})
    preview = shop.preview(shop.segment())
    assert preview["eligible_count"] == 1
    assert preview["excluded_counts"]["no_contact"] == 1 and preview["excluded_counts"]["archived"] == 1


def test_suppressed_contact_excluded_at_preview_and_rechecked_at_execution(shop: Shop, owner_engine: Engine) -> None:
    shop.customer("Ada Example", "ada@example.com", "granted")
    shop.customer("Ben Example", "ben@example.com", "granted")
    seg = shop.segment()
    campaign = shop.campaign(seg)
    submitted = shop.submit(campaign["id"])
    assert submitted["status"] == "in_review" and submitted["snapshot"]["eligible_count"] == 2

    # Ben unsubscribes by other means after the audience was frozen (different letter case and spaces).
    r = shop.marketer.post(
        "/api/v1/marketing/suppressions", json={"value": "  BEN@Example.com ", "reason": "complaint"}
    )
    assert r.status_code == 201 and r.json()["value_normalized"] == "ben@example.com"
    assert shop.preview(seg)["excluded_counts"]["suppressed"] == 1  # excluded at preview...

    assert shop.approve(submitted["proposal_id"]).status_code == 200
    shop.run_worker()
    detail = shop.admin.get(f"/api/v1/approvals/proposals/{submitted['proposal_id']}").json()
    assert detail["status"] == "succeeded"
    with owner_engine.connect() as conn:  # ...and rechecked when the messages are queued
        recipients = (
            conn.execute(text("select recipient from outbox_messages where related_type = 'campaign'")).scalars().all()
        )
        result = conn.execute(text("select result from execution_attempts order by started_at desc limit 1")).scalar()
    assert recipients == ["ada@example.com"]
    assert result["skipped_suppressed"] == 1 and result["enqueued"] == 1
    assert shop.marketer.get(f"/api/v1/marketing/campaigns/{campaign['id']}").json()["status"] == "sent"


def test_consent_withdrawn_after_submit_is_also_skipped(shop: Shop, owner_engine: Engine) -> None:
    shop.customer("Ada Example", "ada@example.com", "granted")
    ben = shop.customer("Ben Example", "ben@example.com", "granted")
    submitted = shop.submit(shop.campaign(shop.segment())["id"])
    shop.owner.post(
        f"/api/v1/crm/customers/{ben}/consents",
        json={
            "channel": "email",
            "purpose": "marketing",
            "status": "withdrawn",
            "source": "staff",
            "evidence": "phone call",
        },
    )
    shop.approve(submitted["proposal_id"])
    shop.run_worker()
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages where related_type = 'campaign'")).scalar() == 1


def test_content_edit_after_submit_requires_new_approval(shop: Shop) -> None:
    shop.customer("Ada Example", "ada@example.com", "granted")
    campaign = shop.campaign(shop.segment())
    first = shop.submit(campaign["id"])
    edited = shop.marketer.post(
        f"/api/v1/marketing/campaigns/{campaign['id']}/revisions",
        json={"subject": "New subject", "body_markdown": "Different words."},
    )
    assert edited.status_code == 201 and edited.json()["revision_no"] == 2
    after = shop.marketer.get(f"/api/v1/marketing/campaigns/{campaign['id']}").json()
    assert after["status"] == "draft" and after["current_revision"]["subject"] == "New subject"
    assert len(after["revisions"]) == 2  # earlier text is kept, never overwritten
    stale = shop.approve(first["proposal_id"])  # the old approval request was withdrawn
    assert stale.status_code == 409
    second = shop.submit(campaign["id"])
    assert second["proposal_id"] != first["proposal_id"]
    assert shop.approve(second["proposal_id"]).status_code == 200
    shop.run_worker()
    assert shop.marketer.get(f"/api/v1/marketing/campaigns/{campaign['id']}").json()["status"] == "sent"


def test_executor_refuses_a_changed_audience_or_revision(shop: Shop, registry: Registry) -> None:
    """Even if a proposal were somehow still open, execution re-checks the exact revision and audience."""
    from app.modules.marketing import service
    from app.modules.marketing.executors import _validate
    from app.modules.marketing.schemas import ApproveCampaignPayload
    from app.platform.context import Context
    from app.platform.db import transaction
    from app.platform.errors import Conflict

    shop.customer("Ada Example", "ada@example.com", "granted")
    campaign = shop.campaign(shop.segment())
    submitted = shop.submit(campaign["id"])
    ctx = Context(
        request_id="t", actor_kind="system", user_id=None, business_id=shop.business,
        permissions=frozenset({"marketing.campaign.read", "marketing.campaign.write"}),
    )  # fmt: skip
    payload = ApproveCampaignPayload(
        campaign_id=uuid.UUID(campaign["id"]),
        revision_id=uuid.UUID(submitted["current_revision"]["id"]),
        snapshot_hash=submitted["snapshot"]["hash"],
    )
    with transaction(ctx) as db:
        _validate(db, ctx, payload)  # unchanged: fine
        with pytest.raises(Conflict):
            _validate(db, ctx, payload.model_copy(update={"snapshot_hash": "0" * 64}))
        with pytest.raises(Conflict):
            _validate(db, ctx, payload.model_copy(update={"revision_id": uuid.uuid4()}))
        assert service.get_campaign(db, ctx, payload.campaign_id).status == "in_review"


def test_unsubscribe_appends_withdrawn_consent_and_suppression(shop: Shop, owner_engine: Engine) -> None:
    ada = shop.customer("Ada Example", "ada@example.com", "granted")
    submitted = shop.submit(shop.campaign(shop.segment())["id"])
    shop.approve(submitted["proposal_id"])
    shop.run_worker()
    with owner_engine.connect() as conn:
        body = conn.execute(
            text("select variables->>'unsubscribe_url' from outbox_messages where related_type = 'campaign'")
        ).scalar()
    assert body and "/api/v1/public/example-cycle-works/unsubscribe?token=" in body
    path = re.sub(r"^https?://[^/]+", "", body)

    visitor = TestClient(shop.app, base_url="https://testserver")
    assert visitor.get(path).status_code == 200
    assert visitor.get(path).status_code == 200  # a second click changes nothing

    consents = shop.owner.get(f"/api/v1/crm/customers/{ada}/consents").json()
    assert consents[0]["status"] == "withdrawn" and consents[0]["source"] == "unsubscribe_link"
    assert [c["status"] for c in consents].count("withdrawn") == 1
    suppressions = shop.marketer.get("/api/v1/marketing/suppressions").json()
    assert [(s["value_normalized"], s["reason"]) for s in suppressions] == [("ada@example.com", "unsubscribed")]
    assert shop.preview(shop.segment("road-bike"))["eligible_count"] == 0


def test_unsubscribe_token_is_signed_and_tied_to_its_business(shop: Shop) -> None:
    ada = shop.customer("Ada Example", "ada@example.com", "granted")
    from app.modules.marketing import service

    good = service.make_token(shop.business, uuid.UUID(ada))
    visitor = TestClient(shop.app, base_url="https://testserver")
    other = make_business("example-garden-store")
    base = "/api/v1/public"
    assert (
        visitor.get(f"{base}/example-cycle-works/unsubscribe", params={"token": good[:-3] + "xyz"}).status_code == 422
    )
    assert visitor.get(f"{base}/example-cycle-works/unsubscribe", params={"token": "garbage"}).status_code == 422
    assert (
        visitor.get(f"{base}/example-garden-store/unsubscribe", params={"token": good}).status_code == 422
    )  # wrong business
    assert other != shop.business
    consents = shop.owner.get(f"/api/v1/crm/customers/{ada}/consents").json()
    assert [c["status"] for c in consents] == ["granted"]  # nothing changed


def test_segment_definition_rejects_unknown_fields(shop: Shop) -> None:
    def post(definition: Any) -> Any:
        return shop.marketer.post("/api/v1/marketing/segments", json={"name": "x", "definition": definition})

    clause = {"field": "tag", "op": "has", "value": "a"}
    assert post({"all": [clause]}).status_code == 201
    assert post({"all": [{"field": "email", "op": "eq", "value": "a@b.c"}]}).status_code == 422  # unknown field
    assert (
        post({"all": [{"field": "tag", "op": "eq", "value": "a"}]}).status_code == 422
    )  # operator not allowed for tag
    assert post({"all": [{**clause, "sql": "1=1"}]}).status_code == 422  # extra key
    assert post({"all": []}).status_code == 422  # never "everyone"
    assert post({"any": [clause]}).status_code == 422
    assert (
        post({"all": [{"field": "last_interaction_at", "op": "older_than_days", "value": "soon"}]}).status_code == 422
    )
    assert post({"all": [{"field": "customer_kind", "op": "eq", "value": "alien"}]}).status_code == 422
    assert (
        post({"all": [{"field": "has_appointment_for_service", "op": "eq", "value": "not-a-uuid"}]}).status_code == 422
    )


def test_cannot_submit_without_eligible_recipients(shop: Shop) -> None:
    shop.customer("Ben Example", "ben@example.com", None)
    campaign = shop.campaign(shop.segment())
    r = shop.marketer.post(f"/api/v1/marketing/campaigns/{campaign['id']}/submit")
    assert r.status_code == 409 and r.json()["code"] == "marketing.no_eligible_recipients"
    assert r.json()["excluded"]["no_consent"] == 1


def test_permissions_and_tenant_isolation(shop: Shop) -> None:
    operator = login_as(shop.app, shop.business, ["operator"])
    assert operator.get("/api/v1/marketing/campaigns").status_code == 403
    auditor = login_as(shop.app, shop.business, ["auditor"])
    assert auditor.post("/api/v1/marketing/segments", json={"name": "x", "definition": {"all": []}}).status_code in (
        403,
        422,
    )
    seg = shop.segment()
    campaign = shop.campaign(seg)
    other = login_as(shop.app, make_business("example-garden-store"), ["owner"])
    assert other.get(f"/api/v1/marketing/campaigns/{campaign['id']}").status_code == 404
    assert other.get(f"/api/v1/marketing/segments/{seg}/preview").status_code == 404
    assert other.get("/api/v1/marketing/suppressions").json() == []


def test_preview_pages_through_the_full_eligible_list(shop: Shop) -> None:
    for i in range(14):
        shop.customer(f"Person {i:02d}", f"p{i:02d}@example.com", "granted")
    seg = shop.segment()
    first = shop.marketer.get(f"/api/v1/marketing/segments/{seg}/preview", params={"limit": 10}).json()
    second = shop.marketer.get(f"/api/v1/marketing/segments/{seg}/preview", params={"offset": 10, "limit": 10}).json()
    assert first["eligible_count"] == 14 and len(first["sample"]) == 10
    assert [m["display_name"] for m in second["members"]] == ["Person 10", "Person 11", "Person 12", "Person 13"]


def test_demo_data_segment_by_service_and_recent_activity(registry: Registry) -> None:
    """The seeded Example Cycle Works: filters that cross into scheduling and the customer's activity."""
    from app.seed import seed

    clock.set_clock(lambda: NOW)
    seed()
    app = create_app(registry)
    marketer = login_as(app, fixture_id("biz-cycle"), ["marketing_manager"])
    services = marketer.get("/api/v1/catalog/items", params={"kind": "service"}).json()["items"]
    brake = next(s for s in services if s["name"] == "Brake service")
    created = marketer.post(
        "/api/v1/marketing/segments",
        json={
            "name": "Brake customers",
            "definition": {"all": [{"field": "has_appointment_for_service", "op": "eq", "value": brake["id"]}]},
        },
    )
    preview = marketer.get(f"/api/v1/marketing/segments/{created.json()['id']}/preview").json()
    assert preview["matched"] >= 1
    assert preview["eligible_count"] + sum(preview["excluded_counts"].values()) == preview["matched"]
    recent = marketer.post(
        "/api/v1/marketing/segments",
        json={
            "name": "Quiet",
            "definition": {"all": [{"field": "last_interaction_at", "op": "older_than_days", "value": 3650}]},
        },
    )
    assert marketer.get(f"/api/v1/marketing/segments/{recent.json()['id']}/preview").json()["matched"] == 0


def test_campaign_messages_use_the_simulated_channel_and_are_delivered(
    shop: Shop, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", "")  # the shared rule: simulated until SMTP is configured
    shop.customer("Ada Example", "ada@example.com", "granted")
    submitted = shop.submit(shop.campaign(shop.segment())["id"])
    shop.approve(submitted["proposal_id"])
    shop.run_worker()
    with owner_engine.connect() as conn:
        rows = conn.execute(text("select channel, status from outbox_messages where related_type = 'campaign'")).all()
    assert [tuple(r) for r in rows] == [
        ("simulated", "delivered")
    ]  # not "failed": nothing real is sent in this version


SMTP_URL = "smtp://mail.example.com:587?from=no-reply@example.com"


@pytest.mark.parametrize(("smtp_url", "label"), [("", "E-mail (simulated)"), (SMTP_URL, "E-mail")])
def test_approval_preview_names_the_channel_the_campaign_will_really_use(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, smtp_url: str, label: str
) -> None:
    """An approver must never be told "simulated" when real e-mail is about to go out (same rule as the outbox)."""
    monkeypatch.setattr(get_settings(), "smtp_url", smtp_url)
    shop.customer("Ada Example", "ada@example.com", "granted")
    submitted = shop.submit(shop.campaign(shop.segment())["id"])
    proposal = shop.admin.get(f"/api/v1/approvals/proposals/{submitted['proposal_id']}").json()
    preview = {line["label"]: line["after"] for line in proposal["current_revision"]["preview"]}
    assert preview["Channel"] == label and preview["Recipients"] == "1"


def test_one_click_unsubscribe_is_public_and_idempotent(shop: Shop) -> None:
    from app.modules.marketing import service

    ada = shop.customer("Ada Example", "ada@example.com", "granted")
    token = service.make_token(shop.business, uuid.UUID(ada))
    path = f"/api/v1/public/example-cycle-works/unsubscribe?token={token}"
    visitor = TestClient(shop.app, base_url="https://testserver")  # no login or CSRF header
    for _ in range(2):
        response = visitor.post(
            path, content="List-Unsubscribe=One-Click", headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert response.status_code == 200
        assert "You are unsubscribed" in response.text
    assert visitor.get(path).status_code == 200  # the original link still works after POST
    consents = shop.owner.get(f"/api/v1/crm/customers/{ada}/consents").json()
    assert [c["status"] for c in consents].count("withdrawn") == 1
    suppressions = shop.marketer.get("/api/v1/marketing/suppressions").json()
    assert [(s["value_normalized"], s["reason"]) for s in suppressions] == [("ada@example.com", "unsubscribed")]


def test_one_click_unsubscribe_validates_signed_token_and_business(shop: Shop) -> None:
    from app.modules.marketing import service

    ada = shop.customer("Ada Example", "ada@example.com", "granted")
    good = service.make_token(shop.business, uuid.UUID(ada))
    make_business("example-garden-store")
    visitor = TestClient(shop.app, base_url="https://testserver")
    for slug, token in (
        ("example-cycle-works", good[:-3] + "xyz"),
        ("example-cycle-works", "garbage"),
        ("example-garden-store", good),
    ):
        response = visitor.post(
            f"/api/v1/public/{slug}/unsubscribe", params={"token": token}, data={"List-Unsubscribe": "One-Click"}
        )
        assert response.status_code == 422 and response.json()["code"] == "marketing.invalid_token"
    assert shop.marketer.get("/api/v1/marketing/suppressions").json() == []
    assert [c["status"] for c in shop.owner.get(f"/api/v1/crm/customers/{ada}/consents").json()] == ["granted"]


@pytest.mark.parametrize("body", ["", "List-Unsubscribe=Other", "Other=One-Click"])
def test_one_click_unsubscribe_requires_rfc8058_body(shop: Shop, body: str) -> None:
    from app.modules.marketing import service

    ada = shop.customer("Ada Example", "ada@example.com", "granted")
    token = service.make_token(shop.business, uuid.UUID(ada))
    visitor = TestClient(shop.app, base_url="https://testserver")
    response = visitor.post(
        "/api/v1/public/example-cycle-works/unsubscribe",
        params={"token": token},
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 422
    assert shop.marketer.get("/api/v1/marketing/suppressions").json() == []
    assert [c["status"] for c in shop.owner.get(f"/api/v1/crm/customers/{ada}/consents").json()] == ["granted"]


def test_one_click_unsubscribe_shares_get_rate_limit(shop: Shop) -> None:
    from app.modules.marketing import service

    ada = shop.customer("Ada Example", "ada@example.com", "granted")
    token = service.make_token(shop.business, uuid.UUID(ada))
    path = f"/api/v1/public/example-cycle-works/unsubscribe?token={token}"
    visitor = TestClient(shop.app, base_url="https://testserver")
    for _ in range(15):
        assert visitor.get(path).status_code == 200
        assert visitor.post(path, data={"List-Unsubscribe": "One-Click"}).status_code == 200
    refused = visitor.get(path)
    assert refused.status_code == 429 and 1 <= int(refused.headers["Retry-After"]) <= 3600  # hourly window (F7)
    refused = visitor.post(path, data={"List-Unsubscribe": "One-Click"})
    assert refused.status_code == 429 and refused.headers["Retry-After"].isdigit()


@pytest.mark.parametrize("change", ["content", "audience", "proposal"])
def test_execution_rejects_replaced_campaign_submission(shop: Shop, owner_engine: Engine, change: str) -> None:
    shop.customer("Ada Example", "ada@example.com", "granted")
    campaign = shop.submit(shop.campaign(shop.segment())["id"])
    assert shop.approve(campaign["proposal_id"]).status_code == 200
    if change == "content":
        r = shop.marketer.post(
            f"/api/v1/marketing/campaigns/{campaign['id']}/revisions",
            json={"subject": "Unapproved subject", "body_markdown": "Unapproved body"},
        )
    else:
        if change == "audience":
            shop.customer("Ben Example", "ben@example.com", "granted")
        r = shop.marketer.patch(
            f"/api/v1/marketing/campaigns/{campaign['id']}", json={"version": campaign["version"], "name": "Edited"}
        )
    assert r.status_code in (200, 201), r.text
    replacement = shop.submit(campaign["id"])
    shop.run_worker()
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages where related_type='campaign'")).scalar() == 0
    current = shop.marketer.get(f"/api/v1/marketing/campaigns/{campaign['id']}").json()
    assert current["status"] == "in_review" and current["proposal_id"] == replacement["proposal_id"]


def test_removing_contact_preserves_campaign_audience_history(shop: Shop, owner_engine: Engine) -> None:
    customer = shop.customer("Ada Example", "ada@example.com", "granted")
    campaign = shop.submit(shop.campaign(shop.segment())["id"])
    detail = shop.owner.get(f"/api/v1/crm/customers/{customer}").json()
    contact = detail["contact_points"][0]["id"]
    response = shop.owner.delete(f"/api/v1/crm/contact-points/{contact}")
    assert response.status_code == 204, response.text
    assert shop.owner.get(f"/api/v1/crm/customers/{customer}").json()["contact_points"] == []
    assert shop.preview(shop.segment())["eligible_count"] == 0
    with owner_engine.connect() as conn:
        assert conn.execute(text("select is_active from contact_points where id=:i"), {"i": contact}).scalar() is False
        assert (
            conn.execute(
                text("select count(*) from audience_members where contact_point_id=:i"), {"i": contact}
            ).scalar()
            == 1
        )
    assert shop.approve(campaign["proposal_id"]).status_code == 200
    shop.run_worker()
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages where related_type='campaign'")).scalar() == 0


@pytest.mark.parametrize("operation", ["revision", "update", "cancel"])
def test_campaign_edit_waits_for_proposal_without_locking_campaign(
    shop: Shop, owner_engine: Engine, operation: str
) -> None:
    import threading

    from app.modules.marketing import permissions, service
    from app.modules.marketing.schemas import CampaignUpdate
    from app.modules.org.testing import system_ctx
    from app.platform.db import transaction

    shop.customer("Ada Example", "ada@example.com", "granted")
    campaign = shop.submit(shop.campaign(shop.segment())["id"])
    assert shop.approve(campaign["proposal_id"]).status_code == 200
    entered = threading.Event()
    errors: list[Exception] = []
    # Signal immediately before the proposal lock query is sent, using the real SQLAlchemy connection.
    from sqlalchemy import event

    def before_execute(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        if (
            threading.current_thread().name == "campaign-edit"
            and "action_proposals" in statement
            and "FOR UPDATE" in statement
        ):
            entered.set()

    from app.platform.db import get_engine

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before_execute)
    ctx = system_ctx(shop.business, frozenset({permissions.CAMPAIGN_WRITE, permissions.PROPOSAL_APPROVE}))

    def edit() -> None:
        try:
            with transaction(ctx) as db:
                cid = uuid.UUID(campaign["id"])
                if operation == "revision":
                    service.add_revision(db, ctx, cid, "New subject", "New body")
                elif operation == "update":
                    service.update_campaign(db, ctx, cid, CampaignUpdate(version=campaign["version"], name="New name"))
                else:
                    service.cancel_campaign(db, ctx, cid)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=edit, name="campaign-edit")
    try:
        with owner_engine.begin() as holder:
            holder.execute(
                text("select id from action_proposals where id=:p for update"), {"p": campaign["proposal_id"]}
            )
            thread.start()
            assert entered.wait(5), "edit never attempted the proposal lock"
            # If editing locked campaign first, this NOWAIT fails, reproducing execution's lock inversion.
            holder.execute(text("select id from campaigns where id=:c for update nowait"), {"c": campaign["id"]})
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
    finally:
        thread.join(5)
        event.remove(engine, "before_cursor_execute", before_execute)
