"""Approvals: export, erasure redaction, and the rule that open proposals block an erasure (M18)."""

from __future__ import annotations

import uuid

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import (
    ActionProposal,
    ApprovalDecision,
    AutonomyCounter,
    AutonomyEvaluation,
    AutonomyRule,
    ExecutionAttempt,
    ProposalRevision,
)

OPEN = ("pending_review", "approved", "executing")


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(
        db,
        ctx,
        [
            ActionProposal,
            ProposalRevision,
            ApprovalDecision,
            ExecutionAttempt,
            AutonomyRule,
            AutonomyEvaluation,
            AutonomyCounter,
        ],
    )
    return export_tables(tables, streaming=streaming)


def _mentions(ids: list[uuid.UUID]):  # type: ignore[no-untyped-def]
    payload = cast(ProposalRevision.payload, Text)
    return or_(*(payload.contains(str(i)) for i in ids))


def links(db: Session, ctx: Context, target: ErasureTarget) -> set[uuid.UUID]:
    """Proposals whose payload refers to the person or to one of their records (an appointment, a conversation)."""
    return set(
        db.scalars(
            select(ProposalRevision.proposal_id).where(
                ProposalRevision.business_id == ctx.business_id, _mentions(target.all_ids)
            )
        )
    )


def check(db: Session, ctx: Context, target: ErasureTarget) -> list[str]:
    """Erasing someone named in a proposal that is still waiting would corrupt what the approver is about to approve."""
    open_ids = db.scalars(
        select(ActionProposal.id)
        .join(ProposalRevision, ProposalRevision.id == ActionProposal.current_revision_id)
        .where(
            ActionProposal.business_id == ctx.business_id,
            ActionProposal.status.in_(OPEN),
            or_(ActionProposal.id.in_(target.all_ids), _mentions(target.all_ids)),
        )
    ).all()
    return (
        [f"{len(open_ids)} open approval request(s) mention this customer: decide or cancel them first"]
        if open_ids
        else []
    )


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Closed proposals are history; the person's name and contact details in their text are replaced."""
    changed = db.scalar(select(func.erase_redact_proposals(ctx.business_id, target.needles, target.all_ids)))
    return {"approval_records_redacted": int(changed or 0)}
