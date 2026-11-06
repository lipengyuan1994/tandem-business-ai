"""Invalidate AI replies on conversation ownership changes, without reversing executor lock order."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.platform import audit, clock, events
from app.platform.context import Context

from .events import ProposalClosed
from .models import ActionProposal, ExecutionAttempt, ProposalRevision


def invalidate_ai_replies(db: Session, ctx: Context, conversation_id: uuid.UUID, generation: int) -> None:
    # Takeover holds the conversation lock; execution holds the proposal lock first. Never wait for
    # that lock here. The executor checks ownership, and the durable event retries skipped rows later.
    rows = db.scalars(
        select(ActionProposal)
        .join(ProposalRevision, ProposalRevision.id == ActionProposal.current_revision_id)
        .where(
            ActionProposal.business_id == ctx.business_id,
            ProposalRevision.business_id == ctx.business_id,
            ActionProposal.conversation_id == conversation_id,
            ActionProposal.origin_kind == "assistant",
            ActionProposal.action_type.in_(["conversations.send_reply", "customer_care.send_message"]),
            ActionProposal.status.in_(["pending_review", "approved", "executing"]),
            # Delayed events must never cancel new replies created after hand-back.
            (
                ProposalRevision.payload["ai_generation"].as_integer().is_(None)
                | (ProposalRevision.payload["ai_generation"].as_integer() < generation)
            ),
        )
        .with_for_update(of=ActionProposal, skip_locked=True)
    )
    for p in rows:
        p.status, p.failure_code, p.version = "cancelled", "conversations.ai_paused", p.version + 1
        db.execute(
            update(ExecutionAttempt)
            .where(
                ExecutionAttempt.business_id == ctx.business_id,
                ExecutionAttempt.proposal_id == p.id,
                ExecutionAttempt.status == "running",
            )
            .values(status="failed", finished_at=clock.now(), error_code="conversations.ai_paused")
        )
        audit.record(
            db,
            ctx,
            action="approvals.proposal.cancelled",
            target_type="proposal",
            target_id=p.id,
            proposal_id=p.id,
            summary="AI reply invalidated: conversation handed to a person",
        )
        events.publish(db, ctx, ProposalClosed(proposal_id=p.id, action_type=p.action_type, outcome="cancelled"))
