"""Independent B2 review regressions. All customer data and provider outputs are synthetic."""

import json
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.modules.ai.customer_care import executor, triage, workflows, writer
from app.modules.ai.customer_care.models import CareCase
from app.modules.ai.customer_care.schemas import Draft, MessageFacts, SendMessage
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.approvals import engine
from app.modules.approvals import public as approvals
from app.modules.approvals.models import ActionProposal, ExecutionAttempt, ProposalRevision
from app.modules.catalog.models import CatalogItem
from app.modules.conversations import public as conversations
from app.modules.conversations.models import Conversation, Message
from app.modules.conversations.publication import publish
from app.modules.crm.models import Customer
from app.modules.notifications import service as mail
from app.modules.notifications.models import OutboxMessage
from app.modules.sales.models import Invoice
from app.modules.scheduling.models import Appointment
from app.platform import clock, events, jobs
from app.platform.db import transaction
from app.platform.errors import Invalid, NotFound
from tests.integration.customer_care_helpers import approve_execute, consent, draft, inbound, queued
from tests.integration.customer_care_helpers import care as care
from tests.integration.test_a1_chat_confirm import shop as shop


def request_only_consent(care):
    consent(care)
    later = clock.now() + timedelta(seconds=1)
    clock.set_clock(lambda: later)
    with transaction(care["ctx"]) as db:
        return conversations.start_conversation(
            db,
            care["ctx"],
            channel="web_chat",
            body="Please answer this request.",
            author_kind="customer",
            customer_id=care["customer"],
            visitor_email="ada@example.com",
            consent_to_contact=True,
        )


@pytest.mark.parametrize("boundary", ["execution", "delivery"])
@pytest.mark.parametrize("kind", ["checkin", "recall", "review_request"])
def test_request_checkbox_never_authorizes_proactive_care(care, boundary, kind):
    consent(care, purpose="marketing", status="granted")
    kwargs = {}
    if kind == "recall":
        with transaction(care["ctx"]) as db:
            a = db.get(Appointment, care["appointment"])
            kwargs["due_month"] = workflows.add_months(a.ends_at.date(), 6).strftime("%Y-%m")
    pid = draft(care, kind, **kwargs)
    assert pid is not None
    if boundary == "delivery":
        approve_execute(care, pid)
        with transaction(care["ctx"]) as db:
            row = db.scalar(select(OutboxMessage).where(OutboxMessage.proposal_id == pid))
            oid, due = row.id, row.not_before
    request_only_consent(care)
    if boundary == "execution":
        approve_execute(care, pid)
        with transaction(care["ctx"]) as db:
            attempt = db.scalar(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == pid))
            assert attempt.result == {"status": "skipped", "reason": "customer_care.no_consent"}
            assert db.scalar(select(OutboxMessage.id).where(OutboxMessage.proposal_id == pid)) is None
    else:
        clock.set_clock(lambda: due)
        assert_blocked_delivery(care, oid, "customer_care.no_consent")


def assert_blocked_delivery(care, oid, reason=None):
    assert mail.deliver(care["business"], oid) == "skipped"
    with transaction(care["ctx"]) as db:
        row = db.get(OutboxMessage, oid)
        assert row.status == "cancelled" and row.attempts == 0
        if reason:
            assert row.last_error == reason


def care_reply(care):
    conv, mid = inbound(care)
    triage.run(care["ctx"], {"message_id": str(mid), "category": "complaint"})
    with transaction(care["ctx"]) as db:
        return conv, db.scalar(select(CareCase.proposal_id))


def approve_only(care, pid):
    detail = care["client"].get(f"/api/v1/approvals/proposals/{pid}").json()
    rev = detail["current_revision"]
    result = care["client"].post(
        f"/api/v1/approvals/proposals/{pid}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    assert result.status_code == 200, result.text
    return uuid.UUID(rev["id"])


@pytest.mark.parametrize("intervention", ["takeover", "reply"])
def test_staff_intervention_invalidates_approved_care_reply(care, intervention):
    conv, pid = care_reply(care)
    rid = approve_only(care, pid)
    with transaction(care["ctx"]) as db:
        if intervention == "reply":
            conversations.send_reply(db, care["ctx"], conv, "A person has answered.")
        else:
            conversations.take_over(db, care["ctx"], conv)
    engine.execute(care["business"], pid, rid)
    with transaction(care["ctx"]) as db:
        p = db.get(ActionProposal, pid)
        assert p.status == "cancelled" and p.failure_code == "conversations.ai_paused"
        assert db.scalar(select(Message.id).where(Message.proposal_id == pid)) is None
        assert db.get(Conversation, conv).ai_mode == "human"


def test_care_executor_rejects_stale_generation_without_invalidation(care, monkeypatch):
    conv, pid = care_reply(care)
    rid = approve_only(care, pid)
    # Simulate the documented skip_locked race: ownership wins, invalidation skips this proposal.
    monkeypatch.setattr(approvals, "invalidate_ai_replies", lambda *args: None)
    with transaction(care["ctx"]) as db:
        conversations.take_over(db, care["ctx"], conv)
    engine.execute(care["business"], pid, rid)
    with transaction(care["ctx"]) as db:
        assert db.scalar(select(Message.id).where(Message.proposal_id == pid)) is None
        assert db.get(Conversation, conv).ai_mode == "human"


def test_staff_reply_after_care_execution_prevents_publication(care):
    conv, pid = care_reply(care)
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        row = db.scalar(select(Message).where(Message.proposal_id == pid))
        ident, due = row.id, row.publish_at
        conversations.send_reply(db, care["ctx"], conv, "A person has answered.")
    clock.set_clock(lambda: due)
    with transaction(care["ctx"]) as db:
        publish(db, care["ctx"], {"message_id": str(ident)})
        assert db.get(Message, ident).publication_state == "cancelled"
        assert db.get(Conversation, conv).ai_mode == "human"


@pytest.mark.parametrize("boundary", ["execution", "delivery"])
def test_staff_intervention_also_blocks_email_care_reply(care, monkeypatch, boundary):
    conv, mid = inbound(care, channel="email")
    triage.run(care["ctx"], {"message_id": str(mid), "category": "complaint"})
    with transaction(care["ctx"]) as db:
        pid = db.scalar(select(CareCase.proposal_id))
    if boundary == "delivery":
        approve_execute(care, pid)
        with transaction(care["ctx"]) as db:
            row = db.scalar(select(OutboxMessage).where(OutboxMessage.proposal_id == pid))
            oid, due = row.id, row.not_before
    else:
        rid = approve_only(care, pid)
    # Exercise execution's own check even when ownership invalidation skips its locked proposal.
    monkeypatch.setattr(approvals, "invalidate_ai_replies", lambda *args: None)
    with transaction(care["ctx"]) as db:
        conversations.send_reply(db, care["ctx"], conv, "A person has answered.")
    if boundary == "execution":
        engine.execute(care["business"], pid, rid)
        with transaction(care["ctx"]) as db:
            attempt = db.scalar(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == pid))
            assert attempt.result == {"status": "skipped", "reason": "conversations.ai_paused"}
            assert db.scalar(select(OutboxMessage.id).where(OutboxMessage.proposal_id == pid)) is None
    else:
        clock.set_clock(lambda: due)
        assert_blocked_delivery(care, oid, "conversations.ai_paused")
    with transaction(care["ctx"]) as db:
        assert db.get(Conversation, conv).ai_mode == "human"


def test_request_consent_preserves_chat_reply_but_blocks_proactive_web_care(care):
    conv = request_only_consent(care)
    with transaction(care["ctx"]) as db:
        mid = conversations.get_thread(db, care["ctx"], conv).messages[0].id
    assert draft(care, source_message_id=mid) is None
    pid = draft(care, "complaint_reply", source_message_id=mid)
    assert pid is not None
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        row = db.scalar(select(Message).where(Message.proposal_id == pid))
        ident, due = row.id, row.publish_at
    clock.set_clock(lambda: due)
    with transaction(care["ctx"]) as db:
        publish(db, care["ctx"], {"message_id": str(ident)})
        assert db.get(Message, ident).publication_state == "published"


@pytest.mark.parametrize("blocked", ["human", "open_proposal", "front_desk_disabled"])
def test_complaint_triage_runs_independently_of_reply_admission(care, registry, blocked):
    _pid, oid, due = queued(care)
    with transaction(care["ctx"]) as db:
        conv = conversations.start_conversation(
            db,
            care["ctx"],
            channel="web_chat",
            author_kind="customer",
            customer_id=care["customer"],
            body="The outcome leaves me deeply unhappy; someone must investigate.",
        )
        mid = conversations.get_thread(db, care["ctx"], conv).messages[0].id
        if blocked == "human":
            conversations.take_over(db, care["ctx"], conv)
        elif blocked == "open_proposal":
            approvals.create_proposal(
                db,
                care["ctx"],
                action_type="conversations.send_reply",
                title="Already pending",
                payload={"conversation_id": str(conv), "body": "Pending response"},
                conversation_id=conv,
            )
    if blocked == "front_desk_disabled":
        response = care["client"].patch("/api/v1/ai/agents/front_desk", json={"version": 0, "enabled": False})
        assert response.status_code == 200
    classification = {"flagged": False, "category": "none", "care_category": "complaint", "care_priority": "high"}
    gateway = FakeGateway([json.dumps(classification)])
    set_router_for_tests(fake_router_by_class(safety=gateway))
    for _ in range(4):
        events.deliver_pending(registry)
        jobs.run_pending(registry)
    with transaction(care["ctx"]) as db:
        case = db.scalar(select(CareCase).where(CareCase.source_message_id == mid))
        assert case is not None and case.status == "open" and case.customer_id == care["customer"]
        assert case.proposal_id is not None
        assert db.get(ActionProposal, case.proposal_id).status == "pending_review"
        assert db.scalar(select(Message.id).where(Message.proposal_id == case.proposal_id)) is None
    clock.set_clock(lambda: due)
    assert_blocked_delivery(care, oid, "customer_care.dispute")
    assert len(gateway.calls) == 1
    from app.modules.ai.customer_care import classification as care_classification

    care_classification.run(care["ctx"], {"message_id": str(mid)})
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("blocked", ["disabled", "hourly_limit"])
def test_independent_classifier_obeys_enable_switch_and_public_cost_limit(care, registry, monkeypatch, blocked):
    from app.modules.ai.customer_care import classification
    from app.platform.config import get_settings

    if blocked == "disabled":
        response = care["client"].patch("/api/v1/ai/agents/customer_care", json={"version": 1, "enabled": False})
        assert response.status_code == 200
    else:
        monkeypatch.setattr(get_settings(), "ai_public_intake_per_hour", 0)
    gateway = FakeGateway([])
    set_router_for_tests(fake_router_by_class(safety=gateway))
    with transaction(care["ctx"]) as db:
        conv = conversations.start_conversation(
            db,
            care["ctx"],
            channel="web_chat",
            body="The outcome is disappointing.",
            author_kind="customer",
            customer_id=care["customer"],
        )
        conversations.take_over(db, care["ctx"], conv)
    for _ in range(2):
        events.deliver_pending(registry)
        jobs.run_pending(registry)
    assert not gateway.calls
    with transaction(care["ctx"]) as db:
        mid = conversations.get_thread(db, care["ctx"], conv).messages[0].id
        assert db.scalar(select(CareCase.id).where(CareCase.source_message_id == mid)) is None
    # The guarded direct worker also honors the switch.
    if blocked == "disabled":
        classification.run(care["ctx"], {"message_id": str(mid)})
        assert not gateway.calls


def test_keyword_complaint_creates_hold_before_jobs_even_when_models_unavailable(care, registry):
    _pid, oid, due = queued(care)
    with transaction(care["ctx"]) as db:
        conv = conversations.start_conversation(
            db,
            care["ctx"],
            channel="web_chat",
            author_kind="customer",
            customer_id=care["customer"],
            body="I need warranty help.",
        )
        conversations.take_over(db, care["ctx"], conv)
    events.deliver_pending(registry)
    clock.set_clock(lambda: due)
    assert_blocked_delivery(care, oid, "customer_care.dispute")


@pytest.mark.parametrize("disabled", [False, True])
def test_linking_anonymous_complaint_binds_dispute_hold(care, registry, monkeypatch, disabled):
    _pid, oid, due = queued(care)
    with transaction(care["ctx"]) as db:
        conv = conversations.start_conversation(
            db, care["ctx"], channel="web_chat", author_kind="visitor", body="A warranty claim."
        )
        mid = conversations.get_thread(db, care["ctx"], conv).messages[0].id
    triage.run(care["ctx"], {"message_id": str(mid), "category": "warranty"})
    if disabled:
        assert (
            care["client"].patch("/api/v1/ai/agents/customer_care", json={"version": 1, "enabled": False}).status_code
            == 200
        )
    original = registry.customer_care_customer_linked
    checked = []

    def under_customer_lock(db, ctx, conversation_id, customer_id):
        with transaction(ctx) as contender:
            assert (
                contender.scalar(
                    text("select pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"booking:{ctx.business_id}:{customer_id}"},
                )
                is False
            )
        checked.append(True)
        original(db, ctx, conversation_id, customer_id)

    monkeypatch.setattr(registry, "customer_care_customer_linked", under_customer_lock)
    with transaction(care["ctx"]) as db:
        conversations.link_customer(db, care["ctx"], conv, care["customer"])
        case = db.scalar(select(CareCase).where(CareCase.source_message_id == mid))
        assert case.customer_id == care["customer"]
    assert checked == [True]
    clock.set_clock(lambda: due)
    assert_blocked_delivery(care, oid, "customer_care.dispute")


@pytest.mark.parametrize("boundary", ["execution", "delivery"])
@pytest.mark.parametrize("change", ["inactive", "disabled", "interval_changed", "not_due"])
def test_recall_rechecks_current_configuration(care, boundary, change):
    with transaction(care["ctx"]) as db:
        a = db.get(Appointment, care["appointment"])
        due_month = workflows.add_months(a.ends_at.date(), 6).strftime("%Y-%m")
    pid = draft(care, "recall", due_month=due_month)
    assert pid is not None
    if boundary == "delivery":
        approve_execute(care, pid)
        with transaction(care["ctx"]) as db:
            outbox = db.scalar(select(OutboxMessage).where(OutboxMessage.proposal_id == pid))
            oid, due = outbox.id, outbox.not_before
    with transaction(care["ctx"]) as db:
        item = db.get(CatalogItem, care["service"])
        if change == "inactive":
            item.status = "archived"
        elif change == "disabled":
            item.recall_interval_months = None
        elif change == "interval_changed":
            item.recall_interval_months = 12
    if change == "not_due":
        original = clock.now()
        clock.set_clock(lambda: original - timedelta(days=30))
    if boundary == "execution":
        approve_execute(care, pid)
        with transaction(care["ctx"]) as db:
            attempt = db.scalar(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == pid))
            assert attempt.result["status"] == "skipped"
            assert attempt.result["reason"] in {"customer_care.recall_off", "customer_care.not_due"}
            assert db.scalar(select(OutboxMessage.id).where(OutboxMessage.proposal_id == pid)) is None
    else:
        if change == "not_due":
            # An interval increase is eligible in the future even though the old queued send is due now.
            with transaction(care["ctx"]) as db:
                db.get(CatalogItem, care["service"]).recall_interval_months = 7
        clock.set_clock(lambda: due)
        assert_blocked_delivery(care, oid)


def test_chinese_review_request_requires_link_and_consent():
    facts = MessageFacts(business_name="示例商家", language="zh")
    with pytest.raises(ValueError, match="review request requires"):
        writer.validate(Draft(subject="示例商家", body="请为我们留下评价。"), facts, "checkin")


@pytest.mark.parametrize("wrong", ["customer", "appointment", "tenant", "missing"])
def test_care_invoice_must_match_scoped_customer_and_appointment(care, other_business, wrong):
    pid = draft(care)
    with transaction(care["ctx"]) as db:
        p = db.get(ActionProposal, pid)
        payload = db.get(ProposalRevision, p.current_revision_id).payload
    invoice_id = uuid.uuid4()
    if wrong != "missing":
        invoice_ctx = replace(care["ctx"], business_id=other_business) if wrong == "tenant" else care["ctx"]
        with transaction(invoice_ctx) as db:
            customer = care["customer"]
            if wrong in {"tenant", "customer"}:
                row = Customer(
                    kind="person", display_name="Another Example", source="staff", business_id=invoice_ctx.business_id
                )
                db.add(row)
                db.flush()
                customer = row.id
            row = Invoice(
                id=invoice_id,
                business_id=invoice_ctx.business_id,
                customer_id=customer,
                bill_to_name="Synthetic",
                currency="USD",
                source_appointment_id=care["appointment"] if wrong == "customer" else None,
            )
            db.add(row)
    with transaction(care["ctx"]) as db:
        with pytest.raises((Invalid, NotFound)):
            executor.validate(db, care["ctx"], SendMessage.model_validate({**payload, "invoice_id": str(invoice_id)}))


def test_care_invoice_matching_customer_and_appointment_is_accepted(care):
    pid = draft(care)
    with transaction(care["ctx"]) as db:
        invoice = Invoice(
            business_id=care["business"],
            customer_id=care["customer"],
            bill_to_name="Synthetic",
            currency="USD",
            source_appointment_id=care["appointment"],
        )
        db.add(invoice)
        db.flush()
        p = db.get(ActionProposal, pid)
        payload = db.get(ProposalRevision, p.current_revision_id).payload
        executor.validate(db, care["ctx"], SendMessage.model_validate({**payload, "invoice_id": str(invoice.id)}))
