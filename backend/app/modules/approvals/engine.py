"""Execution and expiry (07-workflow-engine.md#executing-job-approvalsexecute).

TX-1 claims (approved → executing) and records the attempt; TX-2 runs the executor as the approving user with
permissions re-resolved *now*, and commits local changes + outbox + audit + success together; on a business error
TX-2 rolls back and TX-3 records the failure. A crash inside TX-2 leaves nothing behind and the job retries.
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.platform import audit, clock, events, jobs
from app.platform.canonical import sha256_hex
from app.platform.context import Context, system_context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.ids import new_id
from app.platform.registry import ExecutionMeta, get_registry

from . import autonomy
from .events import ProposalClosed
from .models import ActionProposal, ApprovalDecision, ExecutionAttempt, ProposalRevision
from .service import executor_for

log = structlog.get_logger(__name__)


class ApproverNotAuthorized(DomainError):
    status = 409
    title = "Approver no longer authorised"


def _claim(business_id: uuid.UUID, proposal_id: uuid.UUID, revision_id: uuid.UUID) -> str:
    """TX-1. Returns 'run' (claimed or resumable), or 'done' (already finished / not approved)."""
    ctx = system_context(business_id)
    key = f"{proposal_id}:{revision_id}"
    with transaction(ctx) as db:
        claimed = db.execute(
            update(ActionProposal)
            .where(
                ActionProposal.id == proposal_id,
                ActionProposal.status == "approved",
                ActionProposal.current_revision_id == revision_id,
            )
            .values(status="executing", version=ActionProposal.version + 1)
            .returning(ActionProposal.id)
        ).first()
        if claimed:
            db.execute(
                pg_insert(ExecutionAttempt)
                .values(
                    business_id=business_id,
                    proposal_id=proposal_id,
                    revision_id=revision_id,
                    idempotency_key=key,
                    status="running",
                    id=new_id(),
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            return "run"
        state = db.execute(
            select(ActionProposal.status, ExecutionAttempt.status)
            .join(ExecutionAttempt, ExecutionAttempt.proposal_id == ActionProposal.id, isouter=True)
            .where(ActionProposal.id == proposal_id)
        ).first()
    if state and state[0] == "executing" and state[1] == "running":
        return "run"  # resume after a crash inside TX-2 (it rolled back, so nothing happened yet)
    return "done"


def _approver(db: Session, proposal_id: uuid.UUID, revision_id: uuid.UUID) -> ApprovalDecision:
    row = db.scalar(
        select(ApprovalDecision)
        .where(
            ApprovalDecision.proposal_id == proposal_id,
            ApprovalDecision.revision_id == revision_id,
            ApprovalDecision.decision == "approve",
        )
        .order_by(ApprovalDecision.decided_at.desc())
    )
    assert row is not None
    return row


def _finish(
    db: Session,
    ctx: Context,
    proposal: ActionProposal,
    key: str,
    *,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: DomainError | None = None,
) -> None:
    now = clock.now()
    db.execute(
        update(ExecutionAttempt)
        .where(ExecutionAttempt.idempotency_key == key)
        .values(
            status="succeeded" if ok else "failed",
            finished_at=now,
            result=result or {},
            error_code=None if ok else error.code if error else "approvals.execution_failed",
            error_detail=None if ok else (error.detail if error else None),
        )
    )
    proposal.status = "succeeded" if ok else "failed"
    proposal.failure_code = None if ok else (error.code if error else "approvals.execution_failed")
    proposal.version += 1
    verb = "Executed" if ok else f"Execution failed ({proposal.failure_code})"
    audit.record(
        db,
        ctx,
        action="approvals.proposal." + ("executed" if ok else "failed"),
        target_type="proposal",
        target_id=proposal.id,
        summary=f"{verb}: {proposal.title}",
        proposal_id=proposal.id,
    )
    events.publish(
        db,
        ctx,
        ProposalClosed(
            proposal_id=proposal.id, action_type=proposal.action_type, outcome="succeeded" if ok else "failed"
        ),
    )


def execute(business_id: uuid.UUID, proposal_id: uuid.UUID, revision_id: uuid.UUID) -> str:
    if _claim(business_id, proposal_id, revision_id) == "done":
        return "done"
    key = f"{proposal_id}:{revision_id}"
    failure_ctx = system_context(business_id)
    try:
        with transaction(system_context(business_id)) as db:  # TX-2
            proposal = db.scalar(select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update())
            revision = db.scalar(select(ProposalRevision).where(ProposalRevision.id == revision_id))
            assert proposal is not None and revision is not None
            # Re-check under the row lock: another worker (or the resume path racing it) may have finished already.
            if proposal.status != "executing" or proposal.current_revision_id != revision_id:
                return "done"
            spec = executor_for(proposal.action_type)
            decision = _approver(db, proposal_id, revision_id)
            approver = decision.granted_by if decision.decided_by_kind == "policy" else decision.decided_by
            assert approver is not None
            permissions = identity.effective_permissions(db, approver, business_id)
            ctx = Context(
                request_id=f"exec-{proposal_id.hex[:12]}",
                actor_kind="user",
                user_id=approver,
                business_id=business_id,
                permissions=permissions,
                proposal_id=proposal_id,
            )
            if decision.decided_by_kind == "policy":
                failure_ctx = autonomy.policy_context(ctx, decision)
                ctx = autonomy.check_execution(db, ctx, proposal, decision, spec)
            elif spec.approve_permission not in permissions:
                raise ApproverNotAuthorized("approvals.approver_no_longer_authorized")
            if sha256_hex(revision.payload) != revision.payload_hash or decision.payload_hash != revision.payload_hash:
                raise DomainError("approvals.payload_hash_mismatch")
            db.execute(
                update(ExecutionAttempt).where(ExecutionAttempt.idempotency_key == key).values(acting_user_id=approver)
            )
            model = spec.payload_model.model_validate(revision.payload)
            if decision.decided_via == "chat" or proposal.origin_user_id == approver:
                from .chat import second_person

                if second_person(db, ctx, spec, model):
                    raise ApproverNotAuthorized("approvals.second_person_required")
            result = spec.run(
                db, ctx, model, ExecutionMeta(proposal_id=proposal_id, revision_id=revision_id, idempotency_key=key)
            )
            _finish(db, ctx, proposal, key, ok=True, result=result)
        return "succeeded"
    except DomainError as exc:  # business rule failed at execution time: TX-2 rolled back
        log.info("proposal_execution_failed", proposal_id=str(proposal_id), code=exc.code)
        with transaction(system_context(business_id)) as db:  # TX-3
            proposal = db.scalar(select(ActionProposal).where(ActionProposal.id == proposal_id).with_for_update())
            assert proposal is not None
            if proposal.status == "executing":
                _finish(db, failure_ctx, proposal, key, ok=False, error=exc)
        return "failed"
    except Exception:
        log.error("proposal_execution_crashed", proposal_id=str(proposal_id), error=traceback.format_exc()[-2000:])
        raise  # job retries; the resume path in _claim picks it up


# ----------------------------------------------------------------------------------------------- job handlers
def job_execute(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    execute(ctx.business_id, uuid.UUID(payload["proposal_id"]), uuid.UUID(payload["revision_id"]))


def job_expire_scan(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    """Cron (no tenant): enqueue one expiry job per business that has due proposals."""
    for business_id in db.execute(
        text("select * from businesses_with_due_proposals(:now)"), {"now": clock.now()}
    ).scalars():
        jobs.enqueue(
            db,
            "approvals.expire_business",
            {},
            business_id=business_id,
            dedupe_key=f"expire:{business_id}:{int(clock.now().timestamp()) // 60}",
        )


def job_expire_business(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    due = list(
        db.scalars(
            select(ActionProposal)
            .where(
                ActionProposal.business_id == ctx.business_id,
                ActionProposal.status == "pending_review",
                ActionProposal.expires_at < clock.now(),
            )
            .with_for_update(skip_locked=True)
        )
    )
    for proposal in due:
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
        events.publish(
            db, ctx, ProposalClosed(proposal_id=proposal.id, action_type=proposal.action_type, outcome="expired")
        )


def registered_action_types() -> list[str]:
    return sorted(get_registry().executors)
