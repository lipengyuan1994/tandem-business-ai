"""Proposal lifecycle: create, revise, decide, cancel, read (07-workflow-engine.md#proposals).

Execution and expiry live in engine.py. Approvals never imports business modules: executors come from the
platform registry.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.org import public as org
from app.platform import audit, clock, events, jobs
from app.platform.canonical import sha256_hex
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, Invalid, NotFound
from app.platform.ids import new_id
from app.platform.pagination import clamp_limit, decode_cursor, encode_cursor
from app.platform.registry import ExecutorSpec, get_registry

from . import permissions as P
from .events import ProposalClosed, ProposalCreated, ProposalDecided
from .models import CLOSED, ActionProposal, ApprovalDecision, ExecutionAttempt, ProposalRevision
from .schemas import Evidence, OpenProposal

DEFAULT_TTL = timedelta(hours=24)


def executor_for(action_type: str) -> ExecutorSpec:
    spec = get_registry().executors.get(action_type)
    if spec is None:
        raise Invalid("approvals.unknown_action_type", detail=action_type)
    return spec


def _validated(spec: ExecutorSpec, payload: dict[str, Any]) -> BaseModel:
    try:
        return spec.payload_model.model_validate(payload)
    except ValidationError as exc:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "code": e["type"]} for e in exc.errors()]
        raise Invalid("approvals.invalid_payload", errors=errors) from exc


def _revision(
    db: Session,
    ctx: Context,
    proposal: ActionProposal,
    spec: ExecutorSpec,
    model: BaseModel,
    revision_no: int,
    evidence: list[Evidence],
    summary: str,
) -> ProposalRevision:
    spec.validate(db, ctx, model)  # cheap pre-check (records exist, versions current); not relied on at execution
    payload = model.model_dump(mode="json")
    preview = [{"label": p.label, "before": p.before, "after": p.after} for p in spec.describe(db, ctx, model)]
    revision = ProposalRevision(
        id=new_id(),
        business_id=proposal.business_id,
        proposal_id=proposal.id,
        revision_no=revision_no,
        payload=payload,
        payload_hash=sha256_hex(payload),
        summary=summary[:500],
        evidence=[e.model_dump(mode="json") for e in evidence],
        preview=preview,
        created_by_kind="assistant" if ctx.actor_kind == "assistant" else "staff",
        created_by=ctx.user_id or ctx.on_behalf_of_user_id,
    )
    db.add(revision)
    db.flush()
    return revision


def _can_create(ctx: Context) -> None:
    ctx.require(P.ASSISTANT_USE if ctx.actor_kind == "assistant" else P.PROPOSAL_CREATE)


def create_proposal(
    db: Session,
    ctx: Context,
    *,
    action_type: str,
    payload: dict[str, Any],
    title: str,
    evidence: list[Evidence] | None = None,
    expires_in: timedelta | None = None,
    conversation_id: uuid.UUID | None = None,
) -> ActionProposal:
    _can_create(ctx)
    spec = executor_for(action_type)
    model = _validated(spec, payload)
    assert ctx.business_id is not None
    origin = "assistant" if ctx.actor_kind == "assistant" else ("rule" if ctx.actor_kind == "system" else "staff")
    proposal = ActionProposal(
        id=new_id(),
        business_id=ctx.business_id,
        action_type=action_type,
        status="pending_review",
        origin_kind=origin,
        origin_user_id=ctx.user_id or ctx.on_behalf_of_user_id,
        ai_run_id=ctx.ai_run_id,
        conversation_id=conversation_id,
        title=title,
        expires_at=clock.now() + (expires_in or DEFAULT_TTL),
    )
    db.add(proposal)
    db.flush()
    revision = _revision(db, ctx, proposal, spec, model, 1, evidence or [], title)
    proposal.current_revision_id = revision.id
    audit.record(
        db,
        ctx,
        action="approvals.proposal.created",
        target_type="proposal",
        target_id=proposal.id,
        summary=f"Proposed: {title}",
        proposal_id=proposal.id,
    )
    events.publish(
        db, ctx, ProposalCreated(proposal_id=proposal.id, action_type=action_type, origin_kind=origin, title=title)
    )
    return proposal


def _locked(db: Session, ctx: Context, proposal_id: uuid.UUID) -> ActionProposal:
    stmt = select(ActionProposal).where(ActionProposal.business_id == ctx.business_id, ActionProposal.id == proposal_id)
    proposal = db.scalar(stmt.with_for_update())
    if proposal is None:
        raise NotFound("approvals.not_found")
    return proposal


def _close(db: Session, ctx: Context, proposal: ActionProposal, outcome: str) -> None:
    events.publish(db, ctx, ProposalClosed(proposal_id=proposal.id, action_type=proposal.action_type, outcome=outcome))


def _expire_if_due(db: Session, ctx: Context, proposal: ActionProposal) -> bool:
    if proposal.status == "pending_review" and proposal.expires_at <= clock.now():
        proposal.status, proposal.version = "expired", proposal.version + 1
        audit.record(
            db,
            ctx,
            action="approvals.proposal.expired",
            target_type="proposal",
            target_id=proposal.id,
            summary=f"Expired: {proposal.title}",
            proposal_id=proposal.id,
        )
        _close(db, ctx, proposal, "expired")
        return True
    return False


def revise(
    db: Session, ctx: Context, proposal_id: uuid.UUID, base_revision_id: uuid.UUID, payload: dict[str, Any]
) -> ActionProposal:
    proposal = _locked(db, ctx, proposal_id)
    spec = executor_for(proposal.action_type)
    if not (ctx.has(spec.approve_permission) or (ctx.user_id and ctx.user_id == proposal.origin_user_id)):
        raise Forbidden("auth.permission_denied", detail=spec.approve_permission)
    if _expire_if_due(db, ctx, proposal):
        raise Conflict("approvals.expired")
    if proposal.status != "pending_review":
        raise Conflict("approvals.not_pending", current_status=proposal.status)
    if base_revision_id != proposal.current_revision_id:
        raise Conflict("approvals.revision_changed", current_revision_id=str(proposal.current_revision_id))
    model = _validated(spec, payload)
    last = db.scalar(select(ProposalRevision).where(ProposalRevision.id == proposal.current_revision_id))
    assert last is not None
    evidence = [Evidence.model_validate(e) for e in last.evidence]
    revision = _revision(db, ctx, proposal, spec, model, last.revision_no + 1, evidence, last.summary)
    proposal.current_revision_id, proposal.version = revision.id, proposal.version + 1
    audit.record(
        db,
        ctx,
        action="approvals.proposal.revised",
        target_type="proposal",
        target_id=proposal.id,
        summary=f"Edited (revision {revision.revision_no}): {proposal.title}",
        proposal_id=proposal.id,
    )
    return proposal


def decide(
    db: Session,
    ctx: Context,
    proposal_id: uuid.UUID,
    *,
    decision: str,
    revision_id: uuid.UUID,
    payload_hash: str,
    comment: str | None,
) -> ActionProposal:
    proposal = _locked(db, ctx, proposal_id)
    spec = executor_for(proposal.action_type)
    if not ctx.has(spec.approve_permission):
        raise Forbidden("approvals.not_authorized_to_approve", detail=spec.approve_permission)
    if _expire_if_due(db, ctx, proposal):
        raise Conflict("approvals.expired")
    if proposal.status != "pending_review":
        raise Conflict("approvals.not_pending", current_status=proposal.status)
    revision = db.scalar(select(ProposalRevision).where(ProposalRevision.id == proposal.current_revision_id))
    assert revision is not None
    if revision_id != revision.id or payload_hash != revision.payload_hash:
        raise Conflict("approvals.revision_changed", current_revision_id=str(revision.id))
    if (
        decision == "approve"
        and proposal.origin_kind in {"staff", "assistant"}
        and proposal.origin_user_id == ctx.user_id
        and not org.get_settings(db, ctx).allow_self_approval
    ):
        raise Forbidden("approvals.self_approval_forbidden")
    assert ctx.user_id is not None and ctx.business_id is not None
    roles = sorted(identity.active_roles(db, ctx.user_id, ctx.business_id))
    db.add(
        ApprovalDecision(
            business_id=ctx.business_id,
            proposal_id=proposal.id,
            revision_id=revision.id,
            decision=decision,
            decided_by=ctx.user_id,
            roles_at_decision=roles,
            policy_version=identity.POLICY_VERSION,
            payload_hash=revision.payload_hash,
            comment=comment,
        )
    )
    proposal.status = "approved" if decision == "approve" else "rejected"
    proposal.version += 1
    verb = "Approved" if decision == "approve" else "Rejected"
    audit.record(
        db,
        ctx,
        action=f"approvals.proposal.{proposal.status}",
        target_type="proposal",
        target_id=proposal.id,
        summary=f"{verb}: {proposal.title}",
        proposal_id=proposal.id,
    )
    events.publish(db, ctx, ProposalDecided(proposal_id=proposal.id, decision=decision))
    if decision == "approve":
        jobs.enqueue(
            db,
            "approvals.execute",
            {"proposal_id": str(proposal.id), "revision_id": str(revision.id)},
            business_id=ctx.business_id,
            dedupe_key=f"exec:{proposal.id}:{revision.id}",
            priority=10,
        )
    else:
        _close(db, ctx, proposal, "rejected")
    return proposal


def cancel(db: Session, ctx: Context, proposal_id: uuid.UUID) -> ActionProposal:
    proposal = _locked(db, ctx, proposal_id)
    spec = executor_for(proposal.action_type)
    is_creator = ctx.user_id is not None and ctx.user_id == proposal.origin_user_id
    if not (
        is_creator
        or ctx.has(spec.approve_permission)
        or (ctx.actor_kind == "assistant" and proposal.origin_kind == "assistant")
    ):
        raise Forbidden("auth.permission_denied")
    if proposal.status != "pending_review":
        raise Conflict("approvals.not_pending", current_status=proposal.status)
    proposal.status, proposal.version = "cancelled", proposal.version + 1
    audit.record(
        db,
        ctx,
        action="approvals.proposal.cancelled",
        target_type="proposal",
        target_id=proposal.id,
        summary=f"Cancelled: {proposal.title}",
        proposal_id=proposal.id,
    )
    _close(db, ctx, proposal, "cancelled")
    return proposal


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
) -> list[OpenProposal]:
    if not ctx.has(P.ASSISTANT_USE):
        ctx.require(P.PROPOSAL_READ)
    stmt = select(ActionProposal).where(
        ActionProposal.business_id == ctx.business_id,
        ActionProposal.status.in_(("pending_review", "approved")),
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
    names = identity.get_user_briefs(db, ctx, {d.decided_by for d in decisions})
    return {
        "proposal": proposal,
        "current_revision": next(r for r in revisions if r.id == proposal.current_revision_id),
        "revisions": revisions,
        "decisions": [(d, names[d.decided_by].display_name if d.decided_by in names else "") for d in decisions],
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


__all__ = ["CLOSED", "cancel", "count_pending", "create_proposal", "decide", "get_detail", "list_proposals", "revise"]
