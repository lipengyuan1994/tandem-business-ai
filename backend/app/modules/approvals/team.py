"""Public team projections of approvals, decisions and execution evidence; no AI imports."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.context import Context
from app.platform.models import AuditEvent
from app.platform.pagination import decode_cursor

from .autonomy import RULE_ELIGIBLE_ACTIONS
from .autonomy_schemas import RuleOut
from .autonomy_service import rule_out
from .models import ActionProposal, ApprovalDecision, AutonomyEvaluation, AutonomyRule, ExecutionAttempt
from .reading import delivery_state


def _owned(key: str, action_types: tuple[str, ...]) -> Any:
    return or_(
        ActionProposal.agent_key == key,
        and_(
            ActionProposal.agent_key.is_(None),
            ActionProposal.origin_kind == "assistant",
            ActionProposal.action_type.in_(action_types),
        ),
    )


def summary(db: Session, ctx: Context, key: str, action_types: tuple[str, ...], since: datetime) -> dict[str, Any]:
    if not ctx.has("org.settings.write"):
        ctx.require("approvals.proposal.read")
    owned = _owned(key, action_types)
    base = (ActionProposal.business_id == ctx.business_id, owned)
    created = db.scalar(
        select(func.count()).select_from(ActionProposal).where(*base, ActionProposal.created_at >= since)
    )
    waiting, oldest = db.execute(
        select(func.count(), func.min(ActionProposal.created_at)).where(
            ActionProposal.business_id == ctx.business_id,
            ActionProposal.action_type.in_(action_types),
            ActionProposal.status == "pending_review",
            ActionProposal.expires_at > clock.now(),
        )
    ).one()
    counts = dict(
        db.execute(
            select(ApprovalDecision.decided_by_kind, func.count())
            .join(
                ExecutionAttempt,
                and_(
                    ExecutionAttempt.proposal_id == ApprovalDecision.proposal_id,
                    ExecutionAttempt.revision_id == ApprovalDecision.revision_id,
                    ExecutionAttempt.business_id == ApprovalDecision.business_id,
                ),
            )
            .join(
                ActionProposal,
                and_(
                    ActionProposal.id == ExecutionAttempt.proposal_id,
                    ActionProposal.business_id == ExecutionAttempt.business_id,
                ),
            )
            .where(
                *base,
                ExecutionAttempt.status == "succeeded",
                ExecutionAttempt.finished_at >= since,
                ApprovalDecision.decision == "approve",
            )
            .group_by(ApprovalDecision.decided_by_kind)
        ).all()
    )
    last = db.scalar(select(func.max(ActionProposal.updated_at)).where(*base))
    fallbacks = (
        db.scalar(
            select(func.count())
            .select_from(AutonomyEvaluation)
            .join(
                ActionProposal,
                and_(
                    ActionProposal.id == AutonomyEvaluation.proposal_id,
                    ActionProposal.business_id == AutonomyEvaluation.business_id,
                ),
            )
            .where(*base, AutonomyEvaluation.created_at >= since, AutonomyEvaluation.outcome == "fell_back")
        )
        or 0
    )
    fallbacks += (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .join(
                ActionProposal,
                and_(ActionProposal.id == AuditEvent.proposal_id, ActionProposal.business_id == AuditEvent.business_id),
            )
            .where(*base, AuditEvent.occurred_at >= since, AuditEvent.action == "approvals.proposal.returned_to_person")
        )
        or 0
    )
    rules = {
        r.action_type: r
        for r in db.scalars(
            select(AutonomyRule).where(
                AutonomyRule.business_id == ctx.business_id, AutonomyRule.action_type.in_(action_types)
            )
        )
    }
    return {
        "proposals_created": created or 0,
        "actions_executed_by_people": counts.get("user", 0),
        "actions_executed_by_rules": counts.get("policy", 0),
        "handed_to_person": fallbacks,
        "waiting": {"count": waiting, "oldest_at": oldest},
        "last_activity_at": last,
        "autonomy": [
            rule_out(db, ctx, rules[a])
            if a in rules
            else RuleOut(action_type=a)
            if a in RULE_ELIGIBLE_ACTIONS
            else RuleOut(action_type=a, shadow=False, daily_cap=0)
            for a in action_types
        ],
    }


def _before(stmt: Any, at: Any, ident: Any, cursor: str | None, limit: int | None) -> Any:
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(or_(at < ts, and_(at == ts, ident < rid)))
    stmt = stmt.order_by(at.desc(), ident.desc())
    return stmt.limit(limit) if limit else stmt


def _result_link(result: dict[str, Any], proposal_id: UUID) -> str:
    for field, path in (
        ("reorder_draft_id", "inventory/reorder-drafts"),
        ("purchase_order_id", "purchasing/purchase-orders"),
        ("appointment_id", "scheduling/appointments"),
    ):
        try:
            ident = UUID(str(result.get(field)))
        except ValueError:
            continue
        return f"/api/v1/{path}/{ident}"
    if result.get("outbox_id"):
        return f"/api/v1/notifications/outbox?proposal_id={proposal_id}"
    return f"/api/v1/approvals/proposals/{proposal_id}"


def activity(
    db: Session,
    ctx: Context,
    *,
    key: str | None = None,
    action_types: tuple[str, ...] = (),
    correlation_id: UUID | None = None,
    cursor: str | None = None,
    limit: int | None = 51,
) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    base = [ActionProposal.business_id == ctx.business_id]
    if key:
        base.append(_owned(key, action_types))
    if correlation_id:
        base.append(ActionProposal.correlation_id == correlation_id)
    rows: list[dict[str, Any]] = []

    def item(
        p: ActionProposal,
        ident: UUID,
        at: datetime,
        kind: str,
        status: str,
        *,
        reasons: list[str] | None = None,
        result_link: str | None = None,
        cause: UUID | None = None,
    ) -> dict[str, Any]:
        return {
            "id": ident,
            "agent": p.agent_key or key,
            "kind": kind,
            "created_at": at,
            "status": status,
            "title": p.title,
            "proposal_id": p.id,
            "run_id": p.ai_run_id,
            "action_type": p.action_type,
            "correlation_id": p.correlation_id,
            "causation_id": cause or p.causation_id,
            "depth": p.chain_depth,
            "reasons": reasons or [],
            "result_link": result_link or f"/api/v1/approvals/proposals/{p.id}",
            "delivery_state": delivery_state(db, ctx, p.id),
        }

    for p in db.scalars(
        _before(select(ActionProposal).where(*base), ActionProposal.created_at, ActionProposal.id, cursor, limit)
    ):
        rows.append(
            item(p, p.id, p.created_at, "proposal", p.status, reasons=[p.failure_code] if p.failure_code else [])
        )
    for model, at, kind in (
        (ApprovalDecision, ApprovalDecision.decided_at, "decision"),
        (ExecutionAttempt, ExecutionAttempt.finished_at, "execution"),
        (AutonomyEvaluation, AutonomyEvaluation.created_at, "autonomy"),
        (AuditEvent, AuditEvent.occurred_at, "handoff"),
    ):
        stmt = (
            select(model, ActionProposal)
            .join(
                ActionProposal,
                and_(ActionProposal.id == model.proposal_id, ActionProposal.business_id == model.business_id),
            )
            .where(*base)
        )
        if model is AuditEvent:
            stmt = stmt.where(
                AuditEvent.action.in_(
                    (
                        "approvals.proposal.returned_to_person",
                        "conversations.reply_published",
                        "conversations.reply_stopped",
                        "conversations.reply_cancelled",
                    )
                )
            )
        stmt = stmt.where(at.is_not(None))
        for row, p in db.execute(_before(stmt, at, model.id, cursor, limit)):
            if model is ApprovalDecision:
                status, at_value, reasons = f"{row.decided_by_kind}_{row.decision}", row.decided_at, []
            elif model is ExecutionAttempt:
                status, at_value = row.status, row.finished_at
                reasons = [row.error_code] if row.error_code else []
            elif model is AutonomyEvaluation:
                status, at_value, reasons = row.outcome, row.created_at, row.reasons
            else:
                if row.action == "approvals.proposal.returned_to_person":
                    kind = "handoff"
                    status, at_value, reasons = "handed_to_person", row.occurred_at, [row.changes["reason"]]
                else:
                    kind = "delivery"
                    status, at_value, reasons = row.action.rsplit("_", 1)[-1], row.occurred_at, []
            rows.append(
                item(
                    p,
                    row.id,
                    at_value,
                    kind,
                    status,
                    reasons=reasons,
                    cause=p.id,
                    result_link=_result_link(row.result, p.id) if model is ExecutionAttempt else None,
                )
            )
    return rows


def return_items(db: Session, ctx: Context, *, limit: int, cursor: str | None = None) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    stmt = select(AuditEvent, ActionProposal).join(
        ActionProposal,
        and_(ActionProposal.id == AuditEvent.proposal_id, ActionProposal.business_id == AuditEvent.business_id),
    )
    stmt = stmt.where(
        ActionProposal.business_id == ctx.business_id, AuditEvent.action == "approvals.proposal.returned_to_person"
    )
    return [
        {
            "id": a.id,
            "kind": "autonomy_fallback",
            "proposal_id": p.id,
            "action_type": p.action_type,
            "title": p.title,
            "created_at": a.occurred_at,
            "status": "fell_back",
            "reasons": [a.changes["reason"]],
        }
        for a, p in db.execute(_before(stmt, AuditEvent.occurred_at, AuditEvent.id, cursor, limit))
    ]
