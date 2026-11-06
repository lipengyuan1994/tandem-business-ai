"""Deterministic, transaction-local autonomy evaluation and execution-time delegation checks."""

from __future__ import annotations

from dataclasses import replace
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.org import public as org
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.registry import ExecutorSpec

from .decisions import record_decision
from .models import (
    ActionProposal,
    ApprovalDecision,
    AutonomyCounter,
    AutonomyEvaluation,
    AutonomyRule,
    ProposalRevision,
)

RULE_ELIGIBLE_ACTIONS = frozenset({"inventory.create_reorder_draft", "conversations.send_reply"})


def creation_rule(db: Session, ctx: Context, action_type: str) -> tuple[AutonomyRule | None, bool]:
    if ctx.actor_kind != "assistant" or action_type not in RULE_ELIGIBLE_ACTIONS:
        return None, False
    # Always acquire settings before the rule, as do execution and configuration writes.
    paused = org.autonomy_paused(db, ctx, lock=True)
    rule = db.scalar(
        select(AutonomyRule)
        .where(
            AutonomyRule.business_id == ctx.business_id,
            AutonomyRule.action_type == action_type,
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if rule is not None and rule.active and rule.level == "off":
        raise Conflict("approvals.autonomy_off")
    return rule, paused


def _reserve(db: Session, ctx: Context, rule: AutonomyRule) -> bool:
    if rule.daily_cap == 0:
        return False
    day = clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()
    stmt = pg_insert(AutonomyCounter).values(business_id=ctx.business_id, rule_id=rule.id, day=day, count=1)
    return (
        db.scalar(
            stmt.on_conflict_do_update(
                index_elements=["business_id", "rule_id", "day"],
                set_={"count": AutonomyCounter.count + 1},
                where=AutonomyCounter.count < rule.daily_cap,
            ).returning(AutonomyCounter.count)
        )
        is not None
    )


def policy_context(ctx: Context, decision: ApprovalDecision) -> Context:
    return replace(
        ctx,
        actor_kind="system",
        user_id=None,
        on_behalf_of_user_id=decision.granted_by,
        autonomy_rule_id=decision.rule_id,
        autonomy_rule_version=decision.rule_version,
        proposal_id=decision.proposal_id,
    )


def evaluate(
    db: Session,
    ctx: Context,
    proposal: ActionProposal,
    revision: ProposalRevision,
    spec: ExecutorSpec,
    rule: AutonomyRule | None,
    paused: bool,
) -> None:
    if rule is None or (rule.active and rule.level == "ask"):
        return
    reasons: list[str] = []
    if not rule.active:
        reasons.append("rule_inactive")
    elif rule.level == "draft":
        reasons.append("level_not_supported")
    else:
        if spec.autonomy_reasons is not None:
            reasons.extend(spec.autonomy_reasons(db, ctx, spec.payload_model.model_validate(revision.payload)))
        if paused:
            reasons.append("autonomy_paused")
        if not identity.has_permission_now(db, rule.granted_by, proposal.business_id, spec.approve_permission):
            reasons.append("grantor_not_authorized")
        if not reasons and not _reserve(db, ctx, rule):
            reasons.append("cap_reached")
    outcome = "fell_back" if reasons else "would_act" if rule.shadow else "acted"
    db.add(
        AutonomyEvaluation(
            business_id=proposal.business_id,
            proposal_id=proposal.id,
            rule_id=rule.id,
            rule_version=rule.version,
            outcome=outcome,
            reasons=reasons,
            created_at=clock.now(),
        )
    )
    if outcome == "acted":
        decision = ApprovalDecision(
            business_id=proposal.business_id,
            proposal_id=proposal.id,
            revision_id=revision.id,
            decision="approve",
            decided_by_kind="policy",
            decided_by=None,
            rule_id=rule.id,
            rule_version=rule.version,
            granted_by=rule.granted_by,
            roles_at_decision=sorted(identity.active_roles(db, rule.granted_by, proposal.business_id)),
            policy_version=identity.POLICY_VERSION,
            payload_hash=revision.payload_hash,
            comment=None,
        )
        record_decision(db, policy_context(ctx, decision), proposal, decision)


def check_execution(
    db: Session, ctx: Context, proposal: ActionProposal, decision: ApprovalDecision, spec: ExecutorSpec
) -> Context:
    paused = org.autonomy_paused(db, ctx, lock=True)
    rule = db.scalar(
        select(AutonomyRule)
        .where(
            AutonomyRule.business_id == ctx.business_id,
            AutonomyRule.id == decision.rule_id,
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        paused
        or rule is None
        or not rule.active
        or rule.version != decision.rule_version
        or rule.level not in {"act_notify", "act"}
        or rule.shadow
        or rule.granted_by != decision.granted_by
        or rule.action_type != proposal.action_type
        or proposal.action_type not in RULE_ELIGIBLE_ACTIONS
        or spec.approve_permission not in ctx.permissions
    ):
        raise Conflict("approvals.autonomy_revoked")
    # Reorder execution retains its domain-owned validation and failure codes. Reply safety is rechecked here.
    if proposal.action_type == "conversations.send_reply" and spec.autonomy_reasons is not None:
        revision = db.scalar(
            select(ProposalRevision).where(
                ProposalRevision.business_id == ctx.business_id, ProposalRevision.id == decision.revision_id
            )
        )
        if revision is None or spec.autonomy_reasons(
            db, policy_context(ctx, decision), spec.payload_model.model_validate(revision.payload)
        ):
            raise Conflict("approvals.autonomy_revoked")
    return policy_context(ctx, decision)
