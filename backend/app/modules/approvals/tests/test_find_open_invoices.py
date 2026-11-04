"""Invoice filters use top-level JSONB containment on the current revision, within the business."""

import uuid
from datetime import timedelta

import pytest

from app.modules.approvals import public as approvals
from app.modules.approvals.models import ActionProposal, ProposalRevision
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.canonical import sha256_hex
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.testing.auth import ctx_for

ACTION = "sales.send_payment_reminder"


def proposal(business, payloads, *, status="pending_review", action=ACTION, conversation=None):
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        p = ActionProposal(
            business_id=business,
            action_type=action,
            status=status,
            title="Finance team reminder",
            origin_kind="staff",
            conversation_id=conversation,
            expires_at=clock.now() + timedelta(days=1),
        )
        db.add(p)
        db.flush()
        for n, payload in enumerate(payloads, 1):
            rev = ProposalRevision(
                business_id=business,
                proposal_id=p.id,
                revision_no=n,
                payload=payload,
                payload_hash=sha256_hex(payload),
                summary="Synthetic reminder",
                created_by_kind="staff",
            )
            db.add(rev)
            db.flush()
            p.current_revision_id = rev.id
        return p.id


def test_invoice_filter_matches_only_current_top_level_reference(registry):
    business = make_business()
    other_business = make_business(slug="other-invoice-shop")
    invoice, other_invoice, item, conversation = [uuid.uuid4() for _ in range(4)]
    payload = {
        "invoice_id": str(invoice),
        "invoice_version": 2,
        "body": "Friendly reminder",
        "lines": [{"item_id": str(item), "quantity": "2"}],
    }
    matching = proposal(business, [payload], conversation=conversation)
    approved = proposal(business, [payload], status="approved")
    revised_away = proposal(business, [payload, {"invoice_id": str(other_invoice)}])
    revised_to = proposal(business, [{"invoice_id": str(other_invoice)}, payload])
    other = proposal(business, [{"invoice_id": str(other_invoice)}])
    nested = proposal(business, [{"about": {"invoice_id": str(invoice)}}])
    wrong_action = proposal(business, [payload], action="testing.noop_write")
    foreign = proposal(other_business, [payload])
    for status in ("rejected", "cancelled", "expired", "executing", "succeeded", "failed"):
        proposal(business, [payload], status=status)
    for permission in ("ai.assistant.use", "approvals.proposal.read"):
        ctx = ctx_for(business, {permission})
        with transaction(ctx) as db:
            assert {p.id for p in approvals.find_open_proposals(db, ctx, invoice_id=invoice)} == {
                matching,
                approved,
                revised_to,
                wrong_action,
            }
            assert {
                p.id for p in approvals.find_open_proposals(db, ctx, invoice_id=invoice, action_types=[ACTION])
            } == {
                matching,
                approved,
                revised_to,
            }
            assert {
                p.id
                for p in approvals.find_open_proposals(
                    db, ctx, invoice_id=invoice, item_id=item, conversation_id=conversation
                )
            } == {matching}
            assert {p.id for p in approvals.find_open_proposals(db, ctx, invoice_id=other_invoice)} == {
                other,
                revised_away,
            }
            assert nested not in {p.id for p in approvals.find_open_proposals(db, ctx, invoice_id=invoice)}
            assert approvals.find_open_proposals(db, ctx, invoice_id=uuid.uuid4()) == []
            assert approvals.find_open_proposals(db, ctx, invoice_id=invoice, item_id=uuid.uuid4()) == []
            assert approvals.find_open_proposals(db, ctx, invoice_id=invoice, action_types=[]) == []
    other_ctx = ctx_for(other_business, {"ai.assistant.use"})
    with transaction(other_ctx) as db:
        assert [p.id for p in approvals.find_open_proposals(db, other_ctx, invoice_id=invoice)] == [foreign]
    denied = ctx_for(business)
    with transaction(denied) as db, pytest.raises(Forbidden):
        approvals.find_open_proposals(db, denied, invoice_id=invoice)
