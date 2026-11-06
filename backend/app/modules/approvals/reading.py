"""Scoped proposal queries; lifecycle writes remain in service.py."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.platform.context import Context
from app.platform.errors import NotFound
from app.platform.pagination import clamp_limit, decode_cursor, encode_cursor
from app.platform.registry import DeliveryState, get_registry

from . import permissions as P
from .models import ActionProposal, ApprovalDecision, ExecutionAttempt, ProposalRevision
from .schemas import OpenProposal


# --------------------------------------------------------------------------------------------------- reading
def can_decide(ctx: Context, proposal: ActionProposal) -> bool:
    spec = get_registry().executors.get(proposal.action_type)
    return bool(spec and proposal.status == "pending_review" and ctx.has(spec.approve_permission))


def proposal_exists(db: Session, ctx: Context, proposal_id: uuid.UUID) -> bool:
    """Scoped reference check for other modules; the caller authorizes the operation using the reference."""
    return (
        db.scalar(
            select(ActionProposal.id).where(
                ActionProposal.business_id == ctx.business_id, ActionProposal.id == proposal_id
            )
        )
        is not None
    )


def find_open_proposals(
    db: Session,
    ctx: Context,
    *,
    conversation_id: uuid.UUID | None = None,
    action_types: list[str] | None = None,
    item_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    include_executing: bool = False,
) -> list[OpenProposal]:
    if not ctx.has(P.ASSISTANT_USE):
        ctx.require(P.PROPOSAL_READ)
    stmt = select(ActionProposal).where(
        ActionProposal.business_id == ctx.business_id,
        ActionProposal.status.in_(
            ("pending_review", "approved", "executing") if include_executing else ("pending_review", "approved")
        ),
    )
    if conversation_id is not None:
        stmt = stmt.where(ActionProposal.conversation_id == conversation_id)
    if action_types is not None:
        stmt = stmt.where(ActionProposal.action_type.in_(action_types))
    if item_id is not None or invoice_id is not None:
        stmt = stmt.join(
            ProposalRevision,
            and_(
                ProposalRevision.id == ActionProposal.current_revision_id,
                ProposalRevision.business_id == ActionProposal.business_id,
                ProposalRevision.proposal_id == ActionProposal.id,
            ),
        )
    if item_id is not None:
        stmt = stmt.where(ProposalRevision.payload.contains({"lines": [{"item_id": str(item_id)}]}))
    if invoice_id is not None:
        stmt = stmt.where(ProposalRevision.payload.contains({"invoice_id": str(invoice_id)}))
    return [
        OpenProposal.model_validate(row)
        for row in db.scalars(stmt.order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc()))
    ]


def list_proposals(
    db: Session,
    ctx: Context,
    *,
    status: str | None,
    action_type: str | None,
    mine: bool,
    can_approve: bool,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[ActionProposal], str | None]:
    ctx.require(P.PROPOSAL_READ)
    stmt = select(ActionProposal).where(ActionProposal.business_id == ctx.business_id)
    if status:
        stmt = stmt.where(ActionProposal.status == status)
    if action_type:
        stmt = stmt.where(ActionProposal.action_type == action_type)
    if mine:
        stmt = stmt.where(ActionProposal.origin_user_id == ctx.user_id)
    if can_approve:
        allowed = [t for t, s in get_registry().executors.items() if ctx.has(s.approve_permission)]
        stmt = stmt.where(ActionProposal.action_type.in_(allowed), ActionProposal.status == "pending_review")
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(
            or_(ActionProposal.created_at < ts, and_(ActionProposal.created_at == ts, ActionProposal.id < rid))
        )
    limit = clamp_limit(limit)
    rows = list(db.scalars(stmt.order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc()).limit(limit + 1)))
    page = rows[:limit]
    return page, (encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit and page else None)


def get_detail(db: Session, ctx: Context, proposal_id: uuid.UUID) -> dict[str, Any]:
    ctx.require(P.PROPOSAL_READ)
    proposal = db.scalar(
        select(ActionProposal).where(ActionProposal.business_id == ctx.business_id, ActionProposal.id == proposal_id)
    )
    if proposal is None:
        raise NotFound("approvals.not_found")
    revisions = list(
        db.scalars(
            select(ProposalRevision)
            .where(ProposalRevision.proposal_id == proposal.id)
            .order_by(ProposalRevision.revision_no)
        )
    )
    decisions = list(
        db.scalars(
            select(ApprovalDecision)
            .where(ApprovalDecision.proposal_id == proposal.id)
            .order_by(ApprovalDecision.decided_at)
        )
    )
    attempts = list(
        db.scalars(
            select(ExecutionAttempt)
            .where(ExecutionAttempt.proposal_id == proposal.id)
            .order_by(ExecutionAttempt.started_at)
        )
    )
    names = identity.get_user_briefs(
        db, ctx, {u for d in decisions for u in (d.decided_by, d.granted_by) if u is not None}
    )
    return {
        "proposal": proposal,
        "current_revision": next(r for r in revisions if r.id == proposal.current_revision_id),
        "revisions": revisions,
        "decisions": [
            (d, names[d.decided_by].display_name if d.decided_by is not None and d.decided_by in names else "")
            for d in decisions
        ],
        "grantor_names": {d.id: names[d.granted_by].display_name if d.granted_by in names else "" for d in decisions},
        "attempts": attempts,
    }


def count_pending(db: Session, ctx: Context, *, for_permissions: frozenset[str] | None = None) -> int:
    stmt = select(ActionProposal.action_type).where(
        ActionProposal.business_id == ctx.business_id, ActionProposal.status == "pending_review"
    )
    types = list(db.scalars(stmt))
    if for_permissions is None:
        return len(types)
    executors = get_registry().executors
    return sum(1 for t in types if t in executors and executors[t].approve_permission in for_permissions)


def proposal_notification_state(db: Session, ctx: Context, proposal_id: uuid.UUID) -> dict[str, Any] | None:
    """Internal event consumers: scoped status and delegation, without requiring a human reader."""
    from .autonomy_history import rule_snapshot
    from .models import AutonomyEvaluation

    proposal = db.scalar(
        select(ActionProposal).where(ActionProposal.business_id == ctx.business_id, ActionProposal.id == proposal_id)
    )
    if proposal is None:
        return None
    decision = db.scalar(
        select(ApprovalDecision)
        .where(
            ApprovalDecision.business_id == ctx.business_id,
            ApprovalDecision.proposal_id == proposal.id,
            ApprovalDecision.revision_id == proposal.current_revision_id,
        )
        .order_by(ApprovalDecision.decided_at.desc(), ApprovalDecision.id.desc())
    )
    evaluation = db.scalar(
        select(AutonomyEvaluation).where(
            AutonomyEvaluation.business_id == ctx.business_id, AutonomyEvaluation.proposal_id == proposal.id
        )
    )
    snapshot = (
        rule_snapshot(db, ctx, decision.rule_id, decision.rule_version, proposal.action_type)
        if decision is not None and decision.rule_id is not None and decision.rule_version is not None
        else None
    )
    return {
        "status": proposal.status,
        "origin_kind": proposal.origin_kind,
        "action_type": proposal.action_type,
        "decided_by_kind": decision.decided_by_kind if decision else None,
        "rule_id": decision.rule_id if decision else None,
        "rule_version": decision.rule_version if decision else None,
        "rule_level": snapshot.level if snapshot else None,
        "would_act": evaluation is not None and evaluation.outcome == "would_act",
    }


def delivery_state(db: Session, ctx: Context, proposal_id: uuid.UUID) -> DeliveryState:
    registry = get_registry()
    reader = registry.proposal_delivery_reader
    states = {r(db, ctx, proposal_id) for r in registry.proposal_delivery_readers}
    states.add(reader(db, ctx, proposal_id) if reader else "none")
    priority: tuple[DeliveryState, ...] = (
        "unknown",
        "failed",
        "sending",
        "withheld",
        "scheduled",
        "stopped",
        "published",
        "delivered",
        "none",
    )
    return next(state for state in priority if state in states)


def outbound_authority(db: Session, ctx: Context, proposal_id: uuid.UUID) -> dict[str, Any] | None:
    row = db.execute(
        select(
            ActionProposal.action_type,
            ActionProposal.origin_kind,
            ActionProposal.origin_user_id,
            ProposalRevision.evidence,
        )
        .join(
            ProposalRevision,
            (ProposalRevision.id == ActionProposal.current_revision_id)
            & (ProposalRevision.business_id == ActionProposal.business_id),
        )
        .where(ActionProposal.business_id == ctx.business_id, ActionProposal.id == proposal_id)
    ).first()
    if row is None:
        return None
    result = dict(row._mapping)
    evidence = result.pop("evidence")
    result["source_message_id"] = next(
        (e.get("ref_id") for e in evidence if e.get("kind") == "message" and e.get("ref_type") == "message"), None
    )
    return result
