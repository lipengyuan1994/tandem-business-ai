"""Proposal lifecycle: create, revise, decide, cancel, read (07-workflow-engine.md#proposals).

Execution and expiry live in engine.py. Approvals never imports business modules: executors come from the
platform registry.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.org import public as org
from app.platform import audit, clock, events
from app.platform.canonical import sha256_hex
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, Invalid, NotFound
from app.platform.ids import new_id
from app.platform.registry import ExecutorSpec, get_registry

from . import autonomy
from . import permissions as P
from .decisions import record_decision
from .events import ProposalClosed, ProposalCreated
from .models import CLOSED, ActionProposal, ApprovalDecision, ProposalRevision
from .reading import (
    can_decide as can_decide,
)
from .reading import (
    count_pending as count_pending,
)
from .reading import (
    find_open_proposals as find_open_proposals,
)
from .reading import (
    get_detail as get_detail,
)
from .reading import (
    list_proposals as list_proposals,
)
from .reading import (
    proposal_exists as proposal_exists,
)
from .schemas import Evidence

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
    rule, paused = autonomy.creation_rule(db, ctx, action_type)
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
    autonomy.evaluate(db, ctx, proposal, revision, spec, rule, paused)
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
    record_decision(
        db,
        ctx,
        proposal,
        ApprovalDecision(
            business_id=ctx.business_id,
            proposal_id=proposal.id,
            revision_id=revision.id,
            decision=decision,
            decided_by=ctx.user_id,
            decided_by_kind="user",
            roles_at_decision=roles,
            policy_version=identity.POLICY_VERSION,
            payload_hash=revision.payload_hash,
            comment=comment,
        ),
    )
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


__all__ = ["CLOSED", "cancel", "count_pending", "create_proposal", "decide", "get_detail", "list_proposals", "revise"]
