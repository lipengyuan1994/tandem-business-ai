"""Public request consent: server evidence, matching contacts, relinks and privacy lifecycle."""

from __future__ import annotations

import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.conversations import public as conversations
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.registry import Registry

LABEL = "You may contact me about this request by e-mail."
EVIDENCE = f"v1: {LABEL}"
NOW = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
PUBLIC = "/api/v1/public/example-cycle-works"


@pytest.fixture
def intake(registry: Registry) -> tuple[TestClient, Any]:
    clock.set_clock(lambda: NOW)
    business = make_business()
    app = create_app(registry)
    return TestClient(app, base_url="https://testserver"), login_as(app, business, ["owner"])


def _submit(intake: tuple[TestClient, Any], path: str = "chat", **changes: Any) -> Any:
    visitor, owner = intake
    body = {"name": "Ada Example", "email": "ada@example.com", "consent_to_contact": True}
    if path == "chat":
        body["message"] = "Please help with my request"
    else:
        service = owner.post(
            "/api/v1/catalog/items",
            json={
                "kind": "service",
                "name": "Example service",
                "is_bookable_online": True,
                "duration_minutes": 30,
                "resource_kind": "staff",
            },
        )
        assert service.status_code == 201, service.text
        body.update(
            service_id=service.json()["id"],
            preferred=[{"date": "2026-11-10", "time_from": "09:00", "time_to": "12:00"}],
            phone="+15550100001",
        )
    body.update(changes)
    if body.get("consent_to_contact") is None:
        body.pop("consent_to_contact", None)
    return visitor.post(f"{PUBLIC}/{path}", json=body)


def _customer(owner: Any, email: str = "ada@example.com") -> dict[str, Any]:
    result = owner.post(
        "/api/v1/crm/customers",
        json={
            "display_name": "Ada Example",
            "contact_points": [
                {"type": "email", "value": "primary@example.com", "is_primary": True},
                {"type": "email", "value": email},
            ],
        },
    )
    assert result.status_code == 201, result.text
    return dict(result.json())


def _thread(owner: Any) -> dict[str, Any]:
    return dict(owner.get("/api/v1/conversations").json()["items"][0])


def _link(owner: Any, conversation: dict[str, Any], customer_id: str | None) -> dict[str, Any]:
    result = owner.patch(
        f"/api/v1/conversations/{conversation['id']}",
        json={"version": conversation["version"], "customer_id": customer_id},
    )
    assert result.status_code == 200, result.text
    return dict(result.json())


@pytest.mark.parametrize("path", ["chat", "appointment-requests"])
@pytest.mark.parametrize(
    "ticked,email", [(True, "ada@example.com"), (False, "ada@example.com"), (None, "ada@example.com"), (True, None)]
)
def test_public_submission_evidence(
    intake: tuple[TestClient, Any], owner_engine: Engine, path: str, ticked: bool | None, email: str | None
) -> None:
    result = _submit(intake, path, consent_to_contact=ticked, email=email)
    assert result.status_code == 201, result.text
    thread = _thread(intake[1])
    with owner_engine.connect() as db:
        at, label = db.execute(text("select contact_consent_at, contact_consent_text from conversations")).one()
        assert db.execute(text("select count(*) from consent_records")).scalar_one() == 0
    assert (at, label) == ((NOW, EVIDENCE) if ticked and email else (None, None))
    assert thread["contact_consent_text"] == label
    detail = intake[1].get(f"/api/v1/conversations/{thread['id']}").json()
    assert detail["contact_consent_text"] == label


@pytest.mark.parametrize("path", ["chat", "appointment-requests"])
@pytest.mark.parametrize("field", ["contact_consent_text", "contact_consent_at"])
def test_client_cannot_supply_consent_evidence(intake: tuple[TestClient, Any], path: str, field: str) -> None:
    result = _submit(intake, path, **{field: "Client supplied marketing permission"})
    assert result.status_code == 422
    assert intake[1].get("/api/v1/conversations").json()["items"] == []


@pytest.mark.parametrize("path", ["chat", "appointment-requests"])
@pytest.mark.parametrize("email", [" ADA@EXAMPLE.COM ", "other@example.com"])
def test_link_promotes_matching_email_once(intake: tuple[TestClient, Any], path: str, email: str) -> None:
    assert _submit(intake, path).status_code == 201
    owner = intake[1]
    customer = _customer(owner, email)
    clock.set_clock(lambda: NOW + timedelta(minutes=5))
    linked = _link(owner, _thread(owner), customer["id"])
    linked = _link(owner, linked, customer["id"])
    linked = _link(owner, linked, None)
    _link(owner, linked, customer["id"])
    records = owner.get(f"/api/v1/crm/customers/{customer['id']}/consents").json()
    if email == "other@example.com":
        assert records == []
    else:
        assert len(records) == 1
        consent = records[0]
        assert (consent["channel"], consent["purpose"], consent["status"], consent["source"]) == (
            "email",
            "service_updates",
            "granted",
            "form",
        )
        assert datetime.fromisoformat(consent["recorded_at"]) == NOW
        assert consent["evidence"] == f"Public chat {NOW.isoformat()}: {EVIDENCE} (conversation {linked['id']})"
    assert _thread(owner)["contact_consent_text"] == EVIDENCE


def test_old_consent_does_not_override_later_withdrawal(intake: tuple[TestClient, Any]) -> None:
    assert _submit(intake).status_code == 201
    owner = intake[1]
    customer = _customer(owner)
    clock.set_clock(lambda: NOW + timedelta(minutes=5))
    withdrawal = owner.post(
        f"/api/v1/crm/customers/{customer['id']}/consents",
        json={"channel": "email", "purpose": "service_updates", "status": "withdrawn", "evidence": "Staff request"},
    )
    assert withdrawal.status_code == 201, withdrawal.text
    linked = _link(owner, _thread(owner), customer["id"])
    _link(owner, linked, customer["id"])
    detail = owner.get(f"/api/v1/crm/customers/{customer['id']}").json()
    assert [r["status"] for r in detail["current_consents"]] == ["withdrawn"]
    assert len(owner.get(f"/api/v1/crm/customers/{customer['id']}/consents").json()) == 2


@pytest.mark.parametrize("path", ["chat", "appointment-requests"])
@pytest.mark.parametrize("changes", [{"consent_to_contact": False}, {"consent_to_contact": None}, {"email": None}])
def test_link_without_evidence_never_infers_consent(
    intake: tuple[TestClient, Any], path: str, changes: dict[str, Any]
) -> None:
    assert _submit(intake, path, **changes).status_code == 201
    owner = intake[1]
    customer = _customer(owner)
    _link(owner, _thread(owner), customer["id"])
    assert owner.get(f"/api/v1/crm/customers/{customer['id']}/consents").json() == []


def test_parallel_public_links_promote_only_once(intake: tuple[TestClient, Any], owner_engine: Engine) -> None:
    assert _submit(intake).status_code == 201
    owner = intake[1]
    customer = _customer(owner, "other@example.com")
    thread = _link(owner, _thread(owner), customer["id"])
    assert owner.get(f"/api/v1/crm/customers/{customer['id']}/consents").json() == []
    added = owner.post(
        f"/api/v1/crm/customers/{customer['id']}/contact-points", json={"type": "email", "value": "ada@example.com"}
    )
    assert added.status_code == 201, added.text
    # Two workers relink an already-linked thread through the same API used by executors.
    with owner_engine.connect() as db:
        business_id = db.execute(
            text("select business_id from conversations where id=:id"), {"id": thread["id"]}
        ).scalar_one()
    ctx = Context(
        request_id="parallel-consent",
        actor_kind="user",
        user_id=owner.user_id,
        business_id=business_id,
        permissions=owner.permissions,
    )

    def link() -> None:
        with transaction(ctx) as db:
            conversations.link_customer(db, ctx, UUID(thread["id"]), UUID(customer["id"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(link) for _ in range(2)]
        for result in results:
            result.result(timeout=10)
    assert len(owner.get(f"/api/v1/crm/customers/{customer['id']}/consents").json()) == 1


@pytest.mark.parametrize("regulated", [False, True])
def test_export_and_privacy_lifecycle(
    intake: tuple[TestClient, Any], owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, regulated: bool
) -> None:
    assert _submit(intake).status_code == 201
    owner = intake[1]
    customer = _customer(owner)
    # Visitor-only conversations are found by contact details during erasure/restriction too.
    export = owner.post("/api/v1/admin/exports")
    assert export.status_code == 200, export.text
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        conversations = json.loads(archive.read("conversations/conversations.json"))
        assert conversations[0]["contact_consent_text"] == EVIDENCE
        assert datetime.fromisoformat(conversations[0]["contact_consent_at"]) == NOW
    monkeypatch.setattr(get_settings(), "data_profile", "regulated" if regulated else "standard")
    result = owner.post("/api/v1/admin/erasures", json={"customer_id": customer["id"], "confirm_name": "Ada Example"})
    assert result.status_code == 200, result.text
    with owner_engine.connect() as db:
        row = db.execute(text("select contact_consent_at, contact_consent_text from conversations")).one()
    assert tuple(row) == ((NOW, EVIDENCE) if regulated else (None, None))


def test_widget_and_server_consent_labels_match() -> None:
    from app.modules.conversations.consent import CONTACT_CONSENT_LABEL

    strings = Path(__file__).resolve().parents[3] / "frontend/src/widget/strings.ts"
    assert f'consent: "{CONTACT_CONSENT_LABEL}"' in strings.read_text()
    assert CONTACT_CONSENT_LABEL == LABEL
