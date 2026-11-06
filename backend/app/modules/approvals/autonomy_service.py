"""Scoped owner/admin rule management and proposal-readable Activity."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid
from app.platform.pagination import clamp_limit, decode_cursor, encode_cursor
from app.platform.registry import get_registry

from .autonomy import RULE_ELIGIBLE_ACTIONS
from .autonomy_history import rule_snapshot
from .autonomy_schemas import ActivityOut, ActivityPage, RuleOut, RulePut, RulesOut, ShadowSummary
from .models import ActionProposal, ApprovalDecision, AutonomyEvaluation, AutonomyRule, ExecutionAttempt
from .reading import delivery_state

MANAGE = "org.settings.write"


def _summary(db: Session, ctx: Context, rule: AutonomyRule) -> ShadowSummary:
    counts = dict(
        db.execute(
            select(AutonomyEvaluation.outcome, func.count())
            .where(
                AutonomyEvaluation.business_id == ctx.business_id,
                AutonomyEvaluation.rule_id == rule.id,
                AutonomyEvaluation.created_at >= clock.now() - timedelta(days=30),
            )
            .group_by(AutonomyEvaluation.outcome)
        ).all()
    )
    return ShadowSummary(**{key: counts.get(key, 0) for key in ("would_act", "acted", "fell_back")})


def rule_out(db: Session, ctx: Context, rule: AutonomyRule) -> RuleOut:
    return RuleOut.model_validate(rule).model_copy(update={"summary": _summary(db, ctx, rule)})


def list_rules(db: Session, ctx: Context) -> RulesOut:
    ctx.require(MANAGE)
    rows = {
        r.action_type: r
        for r in db.scalars(
            select(AutonomyRule).where(
                AutonomyRule.business_id == ctx.business_id,
                AutonomyRule.action_type.in_(RULE_ELIGIBLE_ACTIONS),
            )
        )
    }
    return RulesOut(
        paused=org.autonomy_paused(db, ctx),
        items=[
            rule_out(db, ctx, rows[a]) if a in rows else RuleOut(action_type=a)
            for a in sorted(RULE_ELIGIBLE_ACTIONS, key=lambda a: (a != "inventory.create_reorder_draft", a))
        ],
    )


def put_rule(db: Session, ctx: Context, data: RulePut) -> AutonomyRule:
    ctx.require(MANAGE)
    if data.action_type not in RULE_ELIGIBLE_ACTIONS:
        raise Invalid("approvals.autonomy_action_not_eligible")
    spec = get_registry().executors[data.action_type]
    ctx.require(spec.approve_permission)
    assert ctx.business_id is not None and ctx.user_id is not None
    if not identity.has_permission_now(db, ctx.user_id, ctx.business_id, spec.approve_permission):
        raise Conflict("approvals.autonomy_grantor_not_authorized")
    # Covers the initially absent row too, so concurrent version-0 requests return a domain conflict.
    org.autonomy_paused(db, ctx, lock=True)
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"autonomy-rule:{ctx.business_id}:{data.action_type}"},
    )
    row = db.scalar(
        select(AutonomyRule)
        .where(
            AutonomyRule.business_id == ctx.business_id,
            AutonomyRule.action_type == data.action_type,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    version = row.version if row is not None else 0
    if version != data.version:
        raise Conflict("approvals.autonomy_version_conflict", current_version=version)
    if row is None:
        row = AutonomyRule(
            business_id=ctx.business_id,
            granted_by=ctx.user_id,
            **data.model_dump(exclude={"version", "shadow"}),
            shadow=True,
        )
        db.add(row)
    else:
        for key, value in data.model_dump(exclude={"version", "action_type"}).items():
            setattr(row, key, value)
        row.version += 1
        row.granted_by = ctx.user_id
    db.flush()
    audit.record(
        db,
        ctx,
        action="approvals.autonomy_rule.saved",
        target_type="autonomy_rule",
        target_id=row.id,
        summary=f"Autonomy rule {row.action_type} v{row.version}: {row.level}, shadow={row.shadow}",
        changes={"rule_snapshot": {"version": row.version, "level": row.level}},
    )
    return row


def activity(db: Session, ctx: Context, *, limit: int = 50, cursor: str | None = None) -> ActivityPage:
    ctx.require("approvals.proposal.read")
    stmt = (
        select(AutonomyEvaluation, ActionProposal, ExecutionAttempt)
        .join(
            ActionProposal,
            and_(
                ActionProposal.business_id == AutonomyEvaluation.business_id,
                ActionProposal.id == AutonomyEvaluation.proposal_id,
            ),
        )
        .outerjoin(
            ExecutionAttempt,
            and_(
                ExecutionAttempt.business_id == ActionProposal.business_id,
                ExecutionAttempt.proposal_id == ActionProposal.id,
                ExecutionAttempt.revision_id == ActionProposal.current_revision_id,
            ),
        )
        .where(
            AutonomyEvaluation.business_id == ctx.business_id,
        )
    )
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                AutonomyEvaluation.created_at < ts,
                and_(
                    AutonomyEvaluation.created_at == ts,
                    AutonomyEvaluation.id < rid,
                ),
            )
        )
    limit = clamp_limit(limit)
    rows = db.execute(
        stmt.order_by(AutonomyEvaluation.created_at.desc(), AutonomyEvaluation.id.desc()).limit(limit + 1)
    ).all()
    items = []
    for ev, proposal, attempt in rows[:limit]:
        draft_id = attempt.result.get("reorder_draft_id") if attempt is not None else None
        snapshot = rule_snapshot(db, ctx, ev.rule_id, ev.rule_version, proposal.action_type)
        decision = db.scalar(
            select(ApprovalDecision).where(
                ApprovalDecision.business_id == ctx.business_id,
                ApprovalDecision.proposal_id == proposal.id,
                ApprovalDecision.rule_id == ev.rule_id,
                ApprovalDecision.rule_version == ev.rule_version,
                ApprovalDecision.decided_by_kind == "policy",
            )
        )
        grantor = decision.granted_by if decision is not None else snapshot.granted_by if snapshot else None
        names = identity.get_user_briefs(db, ctx, {grantor} if grantor else set())
        items.append(
            ActivityOut(
                delivery_state=delivery_state(db, ctx, proposal.id),
                id=ev.id,
                proposal_id=proposal.id,
                rule_id=ev.rule_id,
                rule_version=ev.rule_version,
                outcome=ev.outcome,
                reasons=ev.reasons,
                created_at=ev.created_at,
                title=proposal.title,
                action_type=proposal.action_type,
                granted_by=grantor,
                granted_by_name=names[grantor].display_name if grantor in names else "",
                status=proposal.status,
                failure_code=proposal.failure_code,
                reorder_draft_id=draft_id,
                result_link=f"/api/v1/inventory/reorder-drafts/{draft_id}" if draft_id else None,
            )
        )
    return ActivityPage(
        items=items,
        next_cursor=(encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit and items else None),
    )
