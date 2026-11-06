"""Synthetic Customer Care fixtures; no external providers."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.modules.ai.customer_care import workflows
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.approvals import engine
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.notifications.models import OutboxMessage
from app.modules.org import public as org
from app.modules.org.models import Business, BusinessSettingsRow
from app.modules.scheduling.models import Appointment
from app.platform import clock
from app.platform.db import transaction
from tests.integration.test_a1_chat_confirm import shop as shop


@pytest.fixture
def care(shop):
    client = shop["client"]
    assert client.patch("/api/v1/ai/agents/customer_care", json={"version": 0, "enabled": True}).status_code == 200
    customer = client.post(
        "/api/v1/crm/customers",
        json={
            "display_name": "Ada Example",
            "given_name": "Ada",
            "contact_points": [{"type": "email", "value": "ada@example.com"}],
            "consents": [
                {
                    "channel": "email",
                    "purpose": "service_updates",
                    "status": "granted",
                    "evidence": "Synthetic test consent",
                }
            ],
        },
    ).json()
    service = client.post(
        "/api/v1/catalog/items",
        json={
            "kind": "service",
            "name": "Example check-up",
            "duration_minutes": 30,
            "resource_kind": "staff",
            "recall_interval_months": 6,
        },
    ).json()
    resource = client.post("/api/v1/scheduling/resources", json={"kind": "staff", "name": "Example provider"}).json()
    shop.update(
        customer=uuid.UUID(customer["id"]), service=uuid.UUID(service["id"]), resource=uuid.UUID(resource["id"])
    )
    with transaction(shop["ctx"]) as db:
        db.get(Business, shop["business"]).public_profile = {"avoid_medical_details": True}
    shop["appointment"] = appointment(shop)
    set_router_for_tests(fake_router_by_class())
    yield shop
    set_router_for_tests(None)
    reset_router_cache()


def appointment(care, *, status="completed", at=None):
    at = at or clock.now() - timedelta(days=180)
    with transaction(care["ctx"]) as db:
        row = Appointment(
            business_id=care["business"],
            customer_id=care["customer"],
            service_id=care["service"],
            resource_id=care["resource"],
            location_id=org.get_default_location(db, care["ctx"]).id,
            starts_at=at,
            ends_at=at + timedelta(minutes=30),
            blocked_until=at + timedelta(minutes=30),
            timezone="America/Denver",
            status=status,
            source="staff",
            hold_expires_at=clock.now() + timedelta(days=1) if status == "held" else None,
        )
        db.add(row)
        db.flush()
        return row.id


def draft(care, kind="checkin", **kwargs):
    return workflows.prepare(care["business"], kind, appointment_id=care["appointment"], **kwargs)


def approve_execute(care, pid):
    from app.modules.identity.testing import login_as

    care["client"] = login_as(care["app"], care["business"], ["owner"], user_id=care["ctx"].user_id)
    client = care["client"]
    detail = client.get(f"/api/v1/approvals/proposals/{pid}").json()
    rev = detail["current_revision"]
    result = client.post(
        f"/api/v1/approvals/proposals/{pid}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    assert result.status_code == 200, result.text
    assert engine.execute(care["business"], pid, uuid.UUID(rev["id"])) == "succeeded"


def consent(care, purpose="service_updates", status="withdrawn"):
    with transaction(care["ctx"]) as db:
        crm.record_consent(
            db,
            care["ctx"],
            care["customer"],
            crm.ConsentIn(channel="email", purpose=purpose, status=status, evidence="Synthetic consent change"),
        )


def settings(care, **values):
    with transaction(care["ctx"]) as db:
        row = db.scalar(select(BusinessSettingsRow))
        row.settings = {**row.settings, **values}


def queued(care):
    pid = draft(care)
    assert pid is not None
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        row = db.scalar(select(OutboxMessage).where(OutboxMessage.proposal_id == pid))
        return pid, row.id, row.not_before


def inbound(care, channel="web_chat"):
    with transaction(care["ctx"]) as db:
        conv = conversations.start_conversation(
            db,
            care["ctx"],
            channel=channel,
            body="The work failed; I need warranty help.",
            author_kind="customer",
            customer_id=care["customer"],
            consent_to_contact=True,
        )
        thread = conversations.get_thread(db, care["ctx"], conv)
        return conv, thread.messages[0].id
