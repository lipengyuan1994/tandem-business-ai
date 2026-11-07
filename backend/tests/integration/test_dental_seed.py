"""The configured clinic uses existing Customer Care and intake workflows."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.main import create_app
from app.modules.ai.customer_care import workflows
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.test_intake_agent import observation, scripted
from app.modules.ai.usecases import intake
from app.modules.approvals import engine
from app.modules.approvals.models import ActionProposal, ProposalRevision
from app.modules.catalog.models import CatalogItem
from app.modules.crm.models import Customer
from app.modules.identity.testing import login_as
from app.modules.org.demo import SYNTHETIC_DEMO_BANNER
from app.modules.org.models import Business
from app.modules.scheduling.models import Appointment, AppointmentResource, Resource
from app.platform import clock
from app.platform.db import transaction
from app.platform.ids import fixture_id
from app.platform.models import Job
from app.seed import _ctx, seed_business


def clinic():
    clock.set_clock(lambda: datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    document = json.loads((Path(__file__).resolve().parents[3] / "data/fixtures.json").read_text())
    business = next(b for b in document["tables"]["businesses"] if b["id"] == "biz-dental")
    counts = seed_business(document["tables"], business)
    assert counts["customers"] == 15 and counts["suppliers"] == 1
    return _ctx(fixture_id("biz-dental"))


def test_clinic_recall_reuses_b2_consent_future_booking_and_minimal_templates(registry):
    ctx = clinic()
    with transaction(ctx) as db:
        assert db.get(Business, ctx.business_id).synthetic_demo
        service = db.scalar(select(CatalogItem).where(CatalogItem.recall_interval_months == 6))
        assert service.name == "Exam and cleaning"
        names = {c.id: c.display_name for c in db.scalars(select(Customer))}
    workflows.recall_scan(ctx, {})
    with transaction(ctx) as db:
        jobs = list(db.scalars(select(Job).where(Job.kind == "ai.customer_care_recall_subject")))
        patient_names = {names[db.get(Appointment, uuid.UUID(j.payload["appointment_id"])).customer_id] for j in jobs}
        assert patient_names == {"Avery Example", "Blair Example", "Emery Example"}
        payload = jobs[0].payload
    workflows.recall_job(ctx, payload)
    with transaction(ctx) as db:
        proposal = db.scalar(select(ActionProposal).where(ActionProposal.action_type == "customer_care.send_message"))
        assert proposal.status == "pending_review"
        message = db.get(ProposalRevision, proposal.current_revision_id).payload
        assert message["kind"] == "recall" and "Exam and cleaning" not in message["body"]
        assert "appointment" in message["body"] and "Example Dental Clinic" in message["body"]


@pytest.mark.parametrize("emergency_after_proposal", [False, True])
def test_dental_intake_fake_router_books_provider_and_chair_end_to_end(registry, emergency_after_proposal):
    ctx = clinic()
    with transaction(ctx) as db:
        service = db.scalar(select(CatalogItem).where(CatalogItem.name == "Exam and cleaning"))
        customer = db.scalar(select(Customer).where(Customer.display_name == "Avery Example"))
        provider = db.scalar(select(Resource).where(Resource.name == "Devon (hygienist)"))
        service_id, customer_id, provider_id = service.id, customer.id, provider.id
    conv, mid = inbound(
        ctx.business_id,
        customer_id=customer_id,
        body="My filling feels a bit rough. Please book a normal exam and cleaning next Tuesday morning.",
        visitor_email=None,
        visitor_phone=None,
    )
    at = "2026-11-10T16:00:00Z"

    def held(request):
        data = observation(request)["data"]
        assert data["slots"][0]["resources"][1]["role"] == "chair"
        return step_tool(
            "hold_slot", service_id=service_id, resource_id=provider_id, customer_id=customer_id, starts_at=at
        )

    def proposed(request):
        return step_tool(
            "propose_booking",
            hold_id=observation(request)["data"]["hold_id"],
            reply={"conversation_id": str(conv), "body": "We have prepared your appointment for staff confirmation."},
        )

    agent = scripted(
        step_tool(
            "find_slots", service_id=service_id, resource_id=provider_id, earliest=at, latest="2026-11-10T17:00:00Z"
        ),
        held,
        proposed,
        step_final(),
    )
    safety = FakeGateway([json.dumps({"flagged": False, "category": "none", "emergency": False})])
    result = intake.run(mid, ctx.business_id, router=fake_router_by_class(agent=agent, safety=safety))
    assert result.status == "succeeded" and len(result.proposal_ids) == 1
    if emergency_after_proposal:
        from app.modules.conversations import service as messages
        from app.modules.conversations.models import Conversation

        with transaction(ctx) as db:
            messages.visitor_message(db, ctx, db.get(Conversation, conv), "I now have severe pain and swelling.")
    owner = login_as(create_app(registry), ctx.business_id, ["owner"])
    detail = owner.get(f"/api/v1/approvals/proposals/{result.proposal_ids[0]}").json()
    rev = detail["current_revision"]
    response = owner.post(
        f"/api/v1/approvals/proposals/{result.proposal_ids[0]}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    assert response.status_code == 200, response.text
    outcome = engine.execute(ctx.business_id, result.proposal_ids[0], uuid.UUID(rev["id"]))
    if emergency_after_proposal:
        assert outcome == "failed"
        with transaction(ctx) as db:
            booking = db.scalar(select(Appointment).where(Appointment.proposal_id == result.proposal_ids[0]))
            assert booking is None or booking.status != "confirmed"
        return
    assert outcome == "succeeded"
    with transaction(ctx) as db:
        booking = db.scalar(select(Appointment).where(Appointment.proposal_id == result.proposal_ids[0]))
        assert booking.status == "confirmed"
        allocated = list(
            db.scalars(select(AppointmentResource).where(AppointmentResource.appointment_id == booking.id))
        )
        assert len(allocated) == 2 and all(a.active for a in allocated)
        assert {db.get(Resource, a.resource_id).kind for a in allocated} == {"staff", "room"}
    assert owner.get("/api/v1/org/business").json()["synthetic_demo_banner"] == SYNTHETIC_DEMO_BANNER
