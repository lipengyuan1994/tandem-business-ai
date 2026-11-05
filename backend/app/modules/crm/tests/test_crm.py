"""M03 crm tests (docs/roadmap/modules/M03-crm.md#tests-must-exist) and P3-08 executors."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.main import create_app
from app.modules.crm import public as crm
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business, system_ctx
from app.platform import jobs
from app.platform.db import transaction
from app.platform.ids import new_id
from app.platform.registry import Registry


@pytest.fixture
def biz() -> uuid.UUID:
    return make_business()


@pytest.fixture
def client(registry: Registry, biz: uuid.UUID) -> Any:
    app = create_app(registry)
    return lambda roles=("owner",), business=None: login_as(app, business or biz, list(roles))


def _ada(c: Any, **extra: Any) -> dict[str, Any]:
    body = {"display_name": "Ada Example", "contact_points": [{"type": "email", "value": "Ada@Example.com "}], **extra}
    r = c.post("/api/v1/crm/customers", json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


def test_duplicate_normalized_email_rejected_for_active_customers(client: Any) -> None:
    c = client()
    ada = _ada(c)
    assert ada["contact_points"][0]["value"] == "Ada@Example.com" and ada["contact_points"][0]["is_primary"]
    dup = c.post(
        "/api/v1/crm/customers",
        json={"display_name": "Ada Again", "contact_points": [{"type": "email", "value": "ada@example.COM"}]},
    )
    assert dup.status_code == 409 and dup.json()["code"] == "crm.contact_point_in_use"
    c.post(f"/api/v1/crm/customers/{ada['id']}/archive", json={"version": ada["version"]})
    assert (
        c.post(
            "/api/v1/crm/customers",
            json={"display_name": "Ada New", "contact_points": [{"type": "email", "value": "ada@example.com"}]},
        ).status_code
        == 201
    )
    assert (
        c.post(
            "/api/v1/crm/customers",
            json={"display_name": "X", "contact_points": [{"type": "email", "value": "not-an-email"}]},
        ).json()["code"]
        == "crm.invalid_email"
    )


def test_candidates_exact_email_scores_one_and_name_similarity_ranked(client: Any) -> None:
    c = client()
    ada = _ada(c)
    c.post("/api/v1/crm/customers", json={"display_name": "Dana Example"})
    c.post("/api/v1/crm/customers", json={"display_name": "Dan Example"})
    by_email = c.get("/api/v1/crm/customers/match", params={"email": "ADA@example.com"}).json()
    assert by_email[0] == {"customer_id": ada["id"], "display_name": "Ada Example", "score": 1.0, "reason": "email"}
    by_name = c.get("/api/v1/crm/customers/match", params={"name": "Dana Exampel"}).json()
    names = [x["display_name"] for x in by_name]
    assert names[0] == "Dana Example" and "Dan Example" in names and all(x["score"] < 0.8 for x in by_name)


def test_consent_is_append_only_and_current_is_latest(client: Any, owner_engine: Engine, biz: uuid.UUID) -> None:
    c = client()
    ada = _ada(
        c,
        consents=[
            {
                "channel": "email",
                "purpose": "marketing",
                "status": "granted",
                "source": "form",
                "evidence": "signup form v1",
            }
        ],
    )
    c.post(
        f"/api/v1/crm/customers/{ada['id']}/consents",
        json={
            "channel": "email",
            "purpose": "marketing",
            "status": "withdrawn",
            "source": "unsubscribe_link",
            "evidence": "clicked unsubscribe",
        },
    )
    history = c.get(f"/api/v1/crm/customers/{ada['id']}/consents").json()
    assert [h["status"] for h in history] == ["withdrawn", "granted"]
    assert [x["status"] for x in c.get(f"/api/v1/crm/customers/{ada['id']}").json()["current_consents"]] == [
        "withdrawn"
    ]
    with pytest.raises(DBAPIError, match="permission denied"), transaction(system_ctx(biz)) as db:
        db.execute(text("update consent_records set status = 'granted'"))


def test_marketing_eligibility_requires_explicit_granted_marketing_consent(client: Any, biz: uuid.UUID) -> None:
    c = client()
    granted = _ada(c, consents=[{"channel": "email", "purpose": "marketing", "status": "granted", "evidence": "form"}])
    c.post(
        "/api/v1/crm/customers",
        json={
            "display_name": "Ben",
            "contact_points": [{"type": "email", "value": "ben@example.com"}],
            "consents": [{"channel": "email", "purpose": "service_updates", "status": "granted", "evidence": "x"}],
        },
    )
    c.post(
        "/api/v1/crm/customers",
        json={"display_name": "Chloe", "contact_points": [{"type": "email", "value": "chloe@example.com"}]},
    )
    with transaction(system_ctx(biz)) as db:
        eligible = crm.list_marketing_eligible(db, system_ctx(biz), "email")
    assert [e.customer_id for e in eligible] == [uuid.UUID(granted["id"])]


def test_timeline_entry_idempotent(biz: uuid.UUID, client: Any) -> None:
    ada = _ada(client())
    ctx = system_ctx(biz)
    entry = crm.TimelineEntryIn(
        customer_id=uuid.UUID(ada["id"]),
        occurred_at=datetime(2026, 11, 5, 17, tzinfo=UTC),
        kind="appointment",
        ref_module="scheduling",
        ref_type="appointment",
        ref_id=new_id(),
        summary="Brake service booked",
    )
    with transaction(ctx) as db:
        crm.add_timeline_entry(db, ctx, entry)
        crm.add_timeline_entry(db, ctx, entry)
    timeline = client().get(f"/api/v1/crm/customers/{ada['id']}/timeline").json()
    assert [t["summary"] for t in timeline] == ["Brake service booked"]
    assert client().get(f"/api/v1/crm/customers/{ada['id']}").json()["last_interaction_at"].startswith("2026-11-05")


def test_executor_update_fails_on_stale_version(client: Any, registry: Registry) -> None:
    owner, operator = client(), client(["operator"])
    ada = _ada(owner)
    body = {
        "action_type": "crm.update_customer",
        "title": "Fix name",
        "payload": {
            "customer_id": ada["id"],
            "customer_version": ada["version"],
            "changes": {"display_name": "Ada E."},
        },
    }
    proposal = operator.post("/api/v1/approvals/proposals", json=body).json()
    assert proposal["current_revision"]["preview"] == [
        {"label": "Display Name", "before": "Ada Example", "after": "Ada E."}
    ]
    owner.patch(
        f"/api/v1/crm/customers/{ada['id']}", json={"version": ada["version"], "preferred_language": "en"}
    )  # someone edits first
    rev = proposal["current_revision"]
    owner.post(
        f"/api/v1/approvals/proposals/{proposal['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    jobs.run_pending(registry)
    detail = owner.get(f"/api/v1/approvals/proposals/{proposal['id']}").json()
    assert detail["status"] == "failed" and detail["failure_code"] == "crm.customer_version_conflict"
    assert owner.get(f"/api/v1/crm/customers/{ada['id']}").json()["display_name"] == "Ada Example"


def test_executor_creates_customer(client: Any, registry: Registry) -> None:
    owner, operator = client(), client(["operator"])
    body = {
        "action_type": "crm.create_customer",
        "title": "New customer from chat",
        "payload": {"display_name": "Eve Example", "contact_points": [{"type": "email", "value": "eve@example.com"}]},
    }
    p = operator.post("/api/v1/approvals/proposals", json=body).json()
    rev = p["current_revision"]
    owner.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    jobs.run_pending(registry)
    assert owner.get(f"/api/v1/approvals/proposals/{p['id']}").json()["status"] == "succeeded"
    assert (
        owner.get("/api/v1/crm/customers", params={"q": "eve"}).json()["items"][0]["primary_email"] == "eve@example.com"
    )


def test_permissions_isolation_and_idempotency(client: Any) -> None:
    owner = client()
    ada = _ada(owner)
    assert client(["inventory_manager"]).post("/api/v1/crm/customers", json={"display_name": "X"}).status_code == 403
    assert client(["inventory_manager"]).get(f"/api/v1/crm/customers/{ada['id']}/consents").status_code == 403
    other = client(["owner"], make_business("example-garden-store"))
    assert other.get(f"/api/v1/crm/customers/{ada['id']}").status_code == 404
    assert other.get("/api/v1/crm/customers/match", params={"email": "ada@example.com"}).json() == []
    key = {"Idempotency-Key": "create-ben-0001"}
    first = owner.post("/api/v1/crm/customers", json={"display_name": "Ben"}, headers=key)
    again = owner.post("/api/v1/crm/customers", json={"display_name": "Ben"}, headers=key)
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]
    stale = owner.patch(f"/api/v1/crm/customers/{ada['id']}", json={"version": ada["version"] - 1, "timezone": "UTC"})
    assert stale.status_code == 409


@pytest.mark.parametrize(
    ("name", "candidate", "expected"),
    [
        ("Bea Example", "Ada Example", False),
        ("Ada Stranger", "Ada Example", False),
        ("Example", "Example", False),
        ("Example Example", "Example", False),
        (None, "Ada Example", False),
        ("  ADA   example ", "Ada Example", True),
        ("Ada Example", "Example, Ada", True),
        ("Ada Middle Example", "Ada Example", True),
        ("D Example", "Dan Example", True),
        ("D Example", "Dana Example", True),
        ("Bea Example", "Ben Example", False),
        ("B Example", "Ada Example", False),
    ],
)
def test_identity_name_overlap_requires_two_distinct_tokens(name: str | None, candidate: str, expected: bool) -> None:
    assert crm.has_identity_name_overlap(name, candidate) is expected
