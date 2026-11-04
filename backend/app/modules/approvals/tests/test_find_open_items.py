"""Item filters inspect only the current revision, within the caller's business."""

import uuid
from datetime import timedelta

from app.modules.approvals import public as approvals
from app.modules.approvals.models import ActionProposal, ProposalRevision
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.canonical import sha256_hex
from app.platform.db import transaction
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for

ACTION = "inventory.create_reorder_draft"


def proposal(
    business: uuid.UUID, revisions: list[list[uuid.UUID]], *, status: str = "pending_review", action: str = ACTION
) -> uuid.UUID:
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        p = ActionProposal(business_id=business, action_type=action, status=status, title="Synthetic reorder",
                           origin_kind="staff", expires_at=clock.now() + timedelta(days=1))  # fmt: skip
        db.add(p)
        db.flush()
        for i, items in enumerate(revisions, 1):
            payload = {"lines": [{"item_id": str(item), "quantity": "2"} for item in items]}
            revision = ProposalRevision(
                business_id=business, proposal_id=p.id, revision_no=i, payload=payload,
                payload_hash=sha256_hex(payload), summary="Synthetic revision", created_by_kind="staff",
            )  # fmt: skip
            db.add(revision)
            db.flush()
            p.current_revision_id = revision.id
        return p.id


def test_find_open_proposals_item_filter_uses_current_revision_and_business(registry: Registry) -> None:
    business = make_business(slug=f"item-filter-{uuid.uuid4().hex[:8]}")
    other_business = make_business(slug=f"other-item-filter-{uuid.uuid4().hex[:8]}")
    target, other_item = uuid.uuid4(), uuid.uuid4()
    matching = proposal(business, [[other_item, target]])  # containment need not match the entire line/array
    other = proposal(business, [[other_item]])
    revised = proposal(business, [[target], [other_item]])  # historical match must not count
    approved = proposal(business, [[target]], status="approved")
    closed = proposal(business, [[target]], status="rejected")
    wrong_action = proposal(business, [[target]], action="testing.noop_write")
    foreign = proposal(other_business, [[target]])

    for permission in ("approvals.proposal.read", "ai.assistant.use"):
        ctx = ctx_for(business, {permission})
        with transaction(ctx) as db:
            assert {p.id for p in approvals.find_open_proposals(db, ctx)} == {
                matching,
                other,
                revised,
                approved,
                wrong_action,
            }
            assert {p.id for p in approvals.find_open_proposals(db, ctx, item_id=target, action_types=[ACTION])} == {
                matching,
                approved,
            }
            assert {
                p.id for p in approvals.find_open_proposals(db, ctx, item_id=other_item, action_types=[ACTION])
            } == {matching, other, revised}
            assert approvals.find_open_proposals(db, ctx, item_id=uuid.uuid4()) == []
            assert approvals.find_open_proposals(db, ctx, item_id=target, action_types=[]) == []
            assert closed not in {p.id for p in approvals.find_open_proposals(db, ctx, item_id=target)}
    ctx = ctx_for(other_business, {"ai.assistant.use"})
    with transaction(ctx) as db:
        assert [p.id for p in approvals.find_open_proposals(db, ctx, item_id=target)] == [foreign]
