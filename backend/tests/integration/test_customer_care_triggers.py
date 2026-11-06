"""Customer Care triggers, human triage, lineage and export."""

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.modules.ai import jobs as ai_jobs
from app.modules.ai import lifecycle
from app.modules.ai.customer_care import triage, workflows
from app.modules.ai.customer_care.models import CareCase
from app.modules.ai.models import AiRun
from app.modules.approvals import public as approvals
from app.modules.approvals.models import ActionProposal, ProposalRevision
from app.modules.conversations import public as conversations
from app.modules.conversations.models import Message
from app.modules.notifications.models import OutboxMessage
from app.platform import clock
from app.platform.context import workflow_scope
from app.platform.db import transaction
from app.platform.models import Job
from tests.integration.customer_care_helpers import appointment, approve_execute, consent, draft, inbound, settings
from tests.integration.customer_care_helpers import care as care
from tests.integration.test_a1_chat_confirm import shop as shop


def test_completed_event_delay_zero_and_disabled(care):
    ctx, event_id = care["ctx"], uuid.uuid4()
    with transaction(ctx) as db:
        workflows.completed_trigger(db, ctx, {"appointment_id": str(care["appointment"])}, event_id)
        job = db.scalar(select(Job).where(Job.kind == "ai.customer_care_checkin"))
        assert job.run_at == clock.now() + timedelta(days=2)
        assert job.correlation_id == job.causation_id == event_id
    settings(care, checkin_delay_days=0)
    with transaction(ctx) as db:
        workflows.completed_trigger(db, ctx, {"appointment_id": str(uuid.uuid4())}, uuid.uuid4())
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "ai.customer_care_checkin")) == 1
    assert draft(care) is None
    settings(care, checkin_delay_days=2)
    care["client"].patch("/api/v1/ai/agents/customer_care", json={"version": 1, "enabled": False})
    assert draft(care) is None
    ai_jobs.guarded(workflows.recall_scan, "customer_care")(ctx, {})
    with transaction(ctx) as db:
        assert db.scalar(select(func.count()).select_from(AiRun)) == 0


def test_recall_eligibility_and_due_month_dedupe(care):
    ctx = care["ctx"]
    workflows.recall_scan(ctx, {})
    with transaction(ctx) as db:
        job = db.scalar(select(Job).where(Job.kind == "ai.customer_care_recall_subject"))
        assert job is not None
        payload, root = job.payload, job.correlation_id
    with workflow_scope(replace(ctx, correlation_id=root)):
        workflows.recall_job(ctx, payload)
    with transaction(ctx) as db:
        p = db.scalar(select(ActionProposal).where(ActionProposal.action_type == "customer_care.send_message"))
        assert p and p.correlation_id == root
        assert db.get(ProposalRevision, p.current_revision_id).payload["due_month"] == payload["due_month"]
    workflows.recall_scan(ctx, {})
    with transaction(ctx) as db:
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "ai.customer_care_recall_subject")) == 1
        )


@pytest.mark.parametrize("blocked", ["future", "consent", "not_due"])
def test_recall_exclusions(care, blocked):
    if blocked == "future":
        appointment(care, status="confirmed", at=clock.now() + timedelta(days=2))
    elif blocked == "consent":
        consent(care)
    else:
        appointment(care, at=clock.now() - timedelta(days=30))
    workflows.recall_scan(care["ctx"], {})
    with transaction(care["ctx"]) as db:
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "ai.customer_care_recall_subject")) == 0
        )


def test_complaint_human_work_and_resolution(care):
    _conv, mid = inbound(care)
    triage.run(care["ctx"], {"message_id": str(mid), "category": "warranty", "priority": "high"})
    items = care["client"].get("/api/v1/ai/work-queue").json()["items"]
    item = next(r for r in items if r["kind"] == "customer_care_case")
    assert item["priority"] == "high" and item["appointment_id"] == str(care["appointment"])
    pid = uuid.UUID(item["proposal_id"])
    with transaction(care["ctx"]) as db:
        from app.modules.ai.customer_care.executor import autonomy_reasons
        from app.modules.ai.customer_care.schemas import SendMessage

        p = db.get(ActionProposal, pid)
        payload = db.get(ProposalRevision, p.current_revision_id).payload
        assert autonomy_reasons(db, care["ctx"], SendMessage.model_validate(payload)) == [
            "customer_care.complaint_requires_human"
        ]
        assert p.status == "pending_review"
    assert draft(care) is None
    response = care["client"].post(f"/api/v1/ai/customer-care/cases/{item['id']}/resolve", json={"version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved"
    assert draft(care)


def test_complaint_web_reply_delay_and_stop(care):
    _conv, mid = inbound(care)
    triage.run(care["ctx"], {"message_id": str(mid), "category": "complaint"})
    with transaction(care["ctx"]) as db:
        pid = db.scalar(select(CareCase.proposal_id))
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        message = db.scalar(select(Message).where(Message.proposal_id == pid, Message.direction == "outbound"))
        assert message.publication_state == "withheld"
        ident, due = message.id, message.publish_at
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 0
    response = care["client"].post(f"/api/v1/conversations/messages/{ident}/stop")
    assert response.status_code == 200, response.text
    from app.modules.conversations.publication import publish

    clock.set_clock(lambda: due)
    with transaction(care["ctx"]) as db:
        publish(db, care["ctx"], {"message_id": str(ident)})
        assert db.get(Message, ident).publication_state == "stopped"


def test_correlation_run_proposal_execution_outbox_export(care):
    correlation, cause = uuid.uuid4(), uuid.uuid4()
    ctx = replace(care["ctx"], correlation_id=correlation, causation_id=cause)
    with workflow_scope(ctx):
        pid = draft(care)
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        p = db.get(ActionProposal, pid)
        run = db.get(AiRun, p.ai_run_id)
        job = db.scalar(select(Job).where(Job.kind == "notifications.deliver"))
        assert run.correlation_id == p.correlation_id == job.correlation_id == correlation
        assert run.causation_id == cause and p.causation_id == run.id and job.causation_id == pid
        exported = lifecycle.export(db, care["ctx"])
        assert len(exported["customer_care_deliveries"]) == 1
        assert "customer_care_cases" in exported
    cards = care["client"].get("/api/v1/ai/agents").json()["items"]
    assert next(c for c in cards if c["key"] == "customer_care")["today"]["proposals_created"] == 1


def test_autonomy_shadow_never_sends(care):
    with transaction(care["ctx"]) as db:
        approvals.put_autonomy_rule(
            db,
            care["ctx"],
            approvals.RulePut(
                action_type="customer_care.send_message",
                level="act_notify",
                shadow=True,
                daily_cap=10,
                active=True,
                version=0,
            ),
        )
    pid = draft(care)
    with transaction(care["ctx"]) as db:
        from app.modules.approvals.models import AutonomyEvaluation

        assert db.get(ActionProposal, pid).status == "pending_review"
        assert db.scalar(select(AutonomyEvaluation.outcome)) == "would_act"
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 0


@pytest.mark.parametrize("classifier", ["safety", "intake"])
def test_intake_classification_creates_immediate_dispute_hold(care, classifier):
    import json

    from app.modules.ai.agent.protocol import FinalStep
    from app.modules.ai.gateway.fake import FakeGateway
    from app.modules.ai.gateway.router import set_router_for_tests
    from app.modules.ai.tests.helpers import fake_router_by_class
    from app.modules.ai.usecases import intake

    _conv, mid = inbound(care)
    safety = {"flagged": False, "category": "none", "care_category": "warranty" if classifier == "safety" else "none"}
    final = FinalStep(
        kind="final", message_to_staff="Warranty concern requires a person", care_category="warranty", needs_human=True
    )
    router = fake_router_by_class(
        safety=FakeGateway([json.dumps(safety)]), agent=FakeGateway([final.model_dump_json()])
    )
    set_router_for_tests(router)
    result = intake.run(mid, care["business"], router=router)
    assert result.status == "succeeded"
    with transaction(care["ctx"]) as db:
        case = db.scalar(select(CareCase))
        job = db.scalar(select(Job).where(Job.kind == "ai.customer_care_triage"))
        assert case.status == "open" and case.category == "warranty"
        assert job and job.correlation_id == db.get(AiRun, result.run_id).correlation_id
    assert draft(care) is None


def test_human_approved_care_web_reply_publishes_while_handed_over(care):
    from app.modules.conversations.publication import publish

    _conv, mid = inbound(care)
    triage.run(care["ctx"], {"message_id": str(mid), "category": "complaint"})
    with transaction(care["ctx"]) as db:
        pid = db.scalar(select(CareCase.proposal_id))
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        row = db.scalar(select(Message).where(Message.proposal_id == pid, Message.direction == "outbound"))
        ident, due = row.id, row.publish_at
    clock.set_clock(lambda: due)
    with transaction(care["ctx"]) as db:
        publish(db, care["ctx"], {"message_id": str(ident)})
        assert db.get(Message, ident).publication_state == "published"


def test_recall_calendar_month_and_latest_completion():
    from datetime import date

    assert workflows.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert workflows.add_months(date(2023, 8, 31), 6) == date(2024, 2, 29)


def test_completed_event_job_produces_checkin_only_after_delay(care, registry):
    from app.modules.scheduling import service as scheduling_service
    from app.modules.scheduling.models import Appointment
    from app.platform import events, jobs

    ctx = care["ctx"]
    with transaction(ctx) as db:
        row = db.get(Appointment, care["appointment"])
        row.status = "confirmed"
        db.flush()
        scheduling_service.complete(db, ctx, row.id, row.version)
    # Deliver the completion event, then its subscriber job. No care writer is due yet.
    for _ in range(3):
        events.deliver_pending(registry)
        jobs.run_pending(registry)
    with transaction(ctx) as db:
        job = db.scalar(select(Job).where(Job.kind == "ai.customer_care_checkin"))
        assert job and job.run_at > clock.now()
        due, correlation = job.run_at, job.correlation_id
        assert (
            db.scalar(select(ActionProposal.id).where(ActionProposal.action_type == "customer_care.send_message"))
            is None
        )
    clock.set_clock(lambda: due)
    jobs.run_pending(registry)
    with transaction(ctx) as db:
        proposal = db.scalar(select(ActionProposal).where(ActionProposal.action_type == "customer_care.send_message"))
        assert proposal is not None and proposal.correlation_id == correlation


def test_complaint_links_related_invoice_and_exports_case(care):
    from app.modules.sales import public as sales

    ctx = care["ctx"]
    # Use the versioned catalog endpoint, then normal sales creation/issue.
    response = care["client"].get(f"/api/v1/catalog/items/{care['service']}")
    assert response.status_code == 200
    care["client"].patch(
        f"/api/v1/catalog/items/{care['service']}", json={"version": response.json()["version"], "price_minor": 8000}
    )
    with transaction(ctx) as db:
        document = sales.from_appointment(db, ctx, care["appointment"])
        document = sales.transition(db, ctx, "invoice", document.id, document.version, "issue")
        invoice_id = document.id
    _conv, mid = inbound(care)
    triage.run(ctx, {"message_id": str(mid), "category": "warranty"})
    with transaction(ctx) as db:
        case = db.scalar(select(CareCase))
        assert case.invoice_id == invoice_id
        assert lifecycle.export(db, ctx)["customer_care_cases"][0]["invoice_id"] == str(invoice_id)


def test_case_without_identified_customer_is_visible_and_counted(care):
    with transaction(care["ctx"]) as db:
        conv = conversations.start_conversation(
            db,
            care["ctx"],
            channel="web_chat",
            author_kind="visitor",
            body="My repaired item broke again.",
            visitor_name="Invented Visitor",
        )
        mid = conversations.get_thread(db, care["ctx"], conv).messages[0].id
    triage.run(care["ctx"], {"message_id": str(mid), "category": "complaint"})
    work = care["client"].get("/api/v1/ai/work-queue").json()["items"]
    item = next(r for r in work if r["kind"] == "customer_care_case")
    assert item["customer_id"] is None and item["proposal_id"] is None
    card = next(r for r in care["client"].get("/api/v1/ai/agents").json()["items"] if r["key"] == "customer_care")
    assert card["waiting"]["count"] == 1 and card["today"]["handed_to_person"] == 1


def test_complaint_actual_nonshadow_rule_still_requires_human(care):
    ctx = care["ctx"]
    with transaction(ctx) as db:
        approvals.put_autonomy_rule(
            db,
            ctx,
            approvals.RulePut(
                action_type="customer_care.send_message",
                level="act_notify",
                shadow=True,
                daily_cap=10,
                active=True,
                version=0,
            ),
        )
        approvals.put_autonomy_rule(
            db,
            ctx,
            approvals.RulePut(
                action_type="customer_care.send_message",
                level="act_notify",
                shadow=False,
                daily_cap=10,
                active=True,
                version=1,
            ),
        )
    _conv, mid = inbound(care)
    triage.run(ctx, {"message_id": str(mid), "category": "complaint"})
    with transaction(ctx) as db:
        from app.modules.approvals.models import AutonomyEvaluation

        row = db.scalar(select(AutonomyEvaluation))
        assert row.outcome == "fell_back" and "customer_care.complaint_requires_human" in row.reasons
        assert db.scalar(select(ActionProposal.status)) == "pending_review"


def test_care_case_permissions_and_foreign_scope(care, registry, other_business):
    from app.platform.testing.auth import client_for

    _conv, mid = inbound(care)
    triage.run(care["ctx"], {"message_id": str(mid), "category": "complaint"})
    with transaction(care["ctx"]) as db:
        ident = db.scalar(select(CareCase.id))
    path = f"/api/v1/ai/customer-care/cases/{ident}"
    reader = client_for(care["app"], registry, care["business"], {"approvals.proposal.read"})
    assert reader.get(path).status_code == 200
    assert reader.post(path + "/resolve", json={"version": 1}).status_code == 403
    foreign = client_for(
        care["app"], registry, other_business, {"approvals.proposal.read", "conversations.proposal.approve"}
    )
    assert foreign.get(path).status_code == 404
    assert foreign.post(path + "/resolve", json={"version": 1}).status_code == 404
    with transaction(care["ctx"]) as db:
        assert db.get(CareCase, ident).status == "open"
