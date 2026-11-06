"""Customer Care execution, delay and Stop safety checks."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.modules.ai.customer_care import policy, writer
from app.modules.ai.customer_care.models import CareCase, CareDelivery
from app.modules.ai.customer_care.schemas import MessageFacts
from app.modules.approvals import public as approvals
from app.modules.approvals.models import ActionProposal, ExecutionAttempt, ProposalRevision
from app.modules.crm.models import Customer
from app.modules.notifications import service as mail
from app.modules.notifications.models import OutboxMessage
from app.modules.org.models import Business
from app.modules.org.schemas import BusinessSettings
from app.platform import clock
from app.platform.db import transaction
from tests.integration.customer_care_helpers import (
    approve_execute,
    consent,
    draft,
    inbound,
    queued,
    settings,
)
from tests.integration.customer_care_helpers import care as care
from tests.integration.test_a1_chat_confirm import shop as shop


@pytest.mark.parametrize(
    "hour,expected",
    [(7, 8), (8, 8), (19, 19), (20, 8), (23, 8)],
)
def test_quiet_hours_next_allowed_time(hour, expected):
    at = datetime(2026, 11, 6, hour, tzinfo=UTC)
    result = policy.allowed_time(at, "UTC", BusinessSettings())
    assert result.hour == expected
    assert result >= at
    if hour >= 20:
        assert result.date() == (at + timedelta(days=1)).date()


def test_minimal_template_never_uses_service_or_notes():
    facts = MessageFacts(
        first_name="Ada",
        service_name="Sensitive treatment",
        service_date="2026-11-03",
        business_name="Example Clinic",
        language="en",
        tone="warm",
    )
    value = writer.template("checkin", facts, minimal=True)
    assert "Sensitive" not in value.body and "2026-11-03" not in value.body
    assert "Example Clinic" in value.body


def test_checkin_pending_human_with_facts_and_dedupe(care):
    pid = draft(care)
    assert pid
    with transaction(care["ctx"]) as db:
        p = db.get(ActionProposal, pid)
        payload = db.get(ProposalRevision, p.current_revision_id).payload
        assert p.status == "pending_review" and p.agent_key == "customer_care"
        assert payload["appointment_id"] == str(care["appointment"])
        assert payload["service_id"] == str(care["service"])
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 0
    assert draft(care) == pid


@pytest.mark.parametrize(
    "change,reason",
    [
        ("consent", "no_consent"),
        ("erased", "customer_erased"),
        ("dispute", "dispute"),
        ("preference", "contact_preference"),
    ],
)
def test_execution_rechecks_live_customer_state(care, change, reason):
    pid = draft(care)
    if change == "consent":
        consent(care)
    elif change == "dispute":
        conv, mid = inbound(care)
        with transaction(care["ctx"]) as db:
            db.add(
                CareCase(
                    business_id=care["business"],
                    customer_id=care["customer"],
                    conversation_id=conv,
                    source_message_id=mid,
                    category="dispute",
                    priority="high",
                    status="open",
                )
            )
    else:
        with transaction(care["ctx"]) as db:
            row = db.get(Customer, care["customer"])
            if change == "erased":
                row.status = "erased"
            else:
                row.preferred_contact_channel = "none"
    approve_execute(care, pid)
    with transaction(care["ctx"]) as db:
        attempt = db.scalar(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == pid))
        assert attempt.result == {"status": "skipped", "reason": f"customer_care.{reason}"}
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 0
    activity = care["client"].get("/api/v1/ai/agents/customer_care/activity").json()["items"]
    assert any(f"customer_care.{reason}" in row["reasons"] and row["status"] == "skipped" for row in activity)


def test_delay_stop_and_duplicate_domain_key(care):
    pid, oid, due = queued(care)
    assert due >= clock.now() + timedelta(seconds=120)
    assert mail.deliver(care["business"], oid) == "skipped"
    assert care["client"].post(f"/api/v1/notifications/outbox/{oid}/stop").status_code == 200
    clock.set_clock(lambda: due + timedelta(seconds=1))
    assert mail.deliver(care["business"], oid) == "skipped"
    with transaction(care["ctx"]) as db:
        payload = db.get(ProposalRevision, db.get(ActionProposal, pid).current_revision_id).payload
        other = approvals.create_proposal(
            db, care["ctx"], action_type="customer_care.send_message", payload=payload, title="Same subject"
        )
    settings(care, allow_self_approval=True)
    approve_execute(care, other.id)
    with transaction(care["ctx"]) as db:
        attempt = db.scalar(select(ExecutionAttempt).where(ExecutionAttempt.proposal_id == other.id))
        assert attempt.result["reason"] == "customer_care.duplicate"
        assert db.scalar(select(func.count()).select_from(CareDelivery)) == 1
        assert db.get(OutboxMessage, oid).attempts == 0


def test_quiet_hours_include_delay_and_reschedule(care):
    with transaction(care["ctx"]) as db:
        db.get(Business, care["business"]).timezone = "UTC"
    base = datetime(2026, 11, 5, 19, 59, tzinfo=UTC)
    clock.set_clock(lambda: base)
    _pid, oid, due = queued(care)
    assert due == datetime(2026, 11, 6, 8, tzinfo=UTC)
    settings(care, customer_care_quiet_end_hour=10)
    clock.set_clock(lambda: due)
    assert mail.deliver(care["business"], oid) == "scheduled"
    with transaction(care["ctx"]) as db:
        assert db.get(OutboxMessage, oid).not_before == due + timedelta(hours=2)
    clock.set_clock(lambda: due + timedelta(hours=2))
    assert mail.deliver(care["business"], oid) == "delivered"


@pytest.mark.parametrize("change", ["consent", "dispute", "minimal"])
def test_delivery_rechecks_after_executor(care, change):
    with transaction(care["ctx"]) as db:
        db.get(Business, care["business"]).public_profile = {}
    _pid, oid, due = queued(care)
    if change == "consent":
        consent(care)
    elif change == "dispute":
        conv, mid = inbound(care)
        with transaction(care["ctx"]) as db:
            db.add(
                CareCase(
                    business_id=care["business"],
                    customer_id=care["customer"],
                    conversation_id=conv,
                    source_message_id=mid,
                    category="complaint",
                    priority="high",
                    status="open",
                )
            )
    else:
        with transaction(care["ctx"]) as db:
            db.get(Business, care["business"]).public_profile = {"avoid_medical_details": True}
    clock.set_clock(lambda: due)
    assert mail.deliver(care["business"], oid) == ("delivered" if change == "minimal" else "skipped")
    with transaction(care["ctx"]) as db:
        row = db.get(OutboxMessage, oid)
        if change == "minimal":
            assert "Example check-up" not in row.variables["body"]
        else:
            assert row.status == "cancelled" and row.attempts == 0


def test_simultaneous_approved_proposals_queue_one_domain_message(care):
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    from app.modules.approvals import engine
    from app.modules.approvals.models import ProposalRevision

    pid = draft(care)
    settings(care, allow_self_approval=True)
    with transaction(care["ctx"]) as db:
        payload = db.get(ProposalRevision, db.get(ActionProposal, pid).current_revision_id).payload
        other = approvals.create_proposal(
            db, care["ctx"], action_type="customer_care.send_message", payload=payload, title="Concurrent same subject"
        )
    revisions = []
    for ident in (pid, other.id):
        detail = care["client"].get(f"/api/v1/approvals/proposals/{ident}").json()
        revision = detail["current_revision"]
        response = care["client"].post(
            f"/api/v1/approvals/proposals/{ident}/decisions",
            json={"decision": "approve", "revision_id": revision["id"], "payload_hash": revision["payload_hash"]},
        )
        assert response.status_code == 200
        revisions.append((ident, uuid.UUID(revision["id"])))
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda pair: engine.execute(care["business"], *pair), revisions))
    assert result == ["succeeded", "succeeded"]
    with transaction(care["ctx"]) as db:
        assert db.scalar(select(func.count()).select_from(CareDelivery)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 1
        assert sorted(r.result["status"] for r in db.scalars(select(ExecutionAttempt))) == ["scheduled", "skipped"]


def test_unrelated_crm_proposal_preserves_contact_opt_out(care):
    from app.modules.crm.models import Customer

    with transaction(care["ctx"]) as db:
        customer = db.get(Customer, care["customer"])
        customer.preferred_contact_channel = "none"
        proposal = approvals.create_proposal(
            db,
            care["ctx"],
            action_type="crm.update_customer",
            title="Correct spelling",
            payload={
                "customer_id": str(customer.id),
                "customer_version": customer.version,
                "changes": {"display_name": "Ada Example Updated"},
            },
        )
        revision = db.get(ProposalRevision, proposal.current_revision_id)
        assert "preferred_contact_channel" not in revision.payload["changes"]
    settings(care, allow_self_approval=True)
    approve_execute(care, proposal.id)
    with transaction(care["ctx"]) as db:
        assert db.get(Customer, care["customer"]).preferred_contact_channel == "none"
