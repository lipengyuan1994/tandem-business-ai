"""One immutable decision / audit / event / enqueue path for human and delegated approvals."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform import audit, events, jobs
from app.platform.context import Context

from .events import ProposalClosed, ProposalDecided
from .models import ActionProposal, ApprovalDecision


def record_decision(db: Session, ctx: Context, proposal: ActionProposal, decision: ApprovalDecision) -> None:
    db.add(decision)
    proposal.status = "approved" if decision.decision == "approve" else "rejected"
    proposal.version += 1
    verb = "Approved" if decision.decision == "approve" else "Rejected"
    summary = f"{verb}: {proposal.title}"
    if decision.decided_via == "chat":
        summary = f"Confirmed in chat: {proposal.title}"
    if decision.decided_by_kind == "policy":
        summary = f"Approved by autonomy rule {decision.rule_id} v{decision.rule_version}: {proposal.title}"
    audit.record(
        db,
        ctx,
        action=f"approvals.proposal.{proposal.status}",
        target_type="proposal",
        target_id=proposal.id,
        summary=summary,
        proposal_id=proposal.id,
    )
    events.publish(db, ctx, ProposalDecided(proposal_id=proposal.id, decision=decision.decision))
    if decision.decision == "approve":
        jobs.enqueue(
            db,
            "approvals.execute",
            {
                "proposal_id": str(proposal.id),
                "revision_id": str(decision.revision_id),
            },
            business_id=ctx.business_id,
            dedupe_key=f"exec:{proposal.id}:{decision.revision_id}",
            priority=10,
        )
    else:
        events.publish(
            db, ctx, ProposalClosed(proposal_id=proposal.id, action_type=proposal.action_type, outcome="rejected")
        )
