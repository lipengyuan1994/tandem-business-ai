"""Business-scoped operational proposal and fallback reads for the AI work views."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.platform import clock
from app.platform.context import Context
from app.platform.pagination import decode_cursor
from app.platform.registry import get_registry

from .models import ActionProposal, AutonomyEvaluation


def proposal_items(
    db: Session, ctx: Context, *, failed: bool = False, limit: int = 50, cursor: str | None = None
) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    stmt = select(ActionProposal).where(
        ActionProposal.business_id == ctx.business_id,
        ActionProposal.status == ("failed" if failed else "pending_review"),
    )
    if not failed:
        stmt = stmt.where(ActionProposal.expires_at > clock.now())
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(
            or_(ActionProposal.created_at < ts, and_(ActionProposal.created_at == ts, ActionProposal.id < rid))
        )
    out = []
    for row in db.scalars(stmt.order_by(ActionProposal.created_at.desc(), ActionProposal.id.desc()).limit(limit)):
        spec = get_registry().executors.get(row.action_type)
        permission = spec.approve_permission if spec else None
        roles = sorted(role for role, permissions in identity.ROLES.items() if permission in permissions)
        out.append(
            {
                "id": row.id,
                "kind": "proposal",
                "proposal_id": row.id,
                "action_type": row.action_type,
                "title": row.title,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "status": row.status,
                "approve_permission": permission,
                "waiting_on_roles": roles,
                "failure_code": row.failure_code,
            }
        )
    return out


def fallback_items(db: Session, ctx: Context, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
    ctx.require("approvals.proposal.read")
    stmt = (
        select(AutonomyEvaluation, ActionProposal)
        .join(
            ActionProposal,
            (ActionProposal.id == AutonomyEvaluation.proposal_id)
            & (ActionProposal.business_id == AutonomyEvaluation.business_id),
        )
        .where(
            AutonomyEvaluation.business_id == ctx.business_id,
            AutonomyEvaluation.outcome == "fell_back",
            AutonomyEvaluation.created_at >= clock.now() - timedelta(days=7),
        )
    )
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                AutonomyEvaluation.created_at < ts,
                and_(AutonomyEvaluation.created_at == ts, AutonomyEvaluation.id < rid),
            )
        )
    return [
        {
            "id": e.id,
            "kind": "autonomy_fallback",
            "proposal_id": p.id,
            "action_type": p.action_type,
            "title": p.title,
            "created_at": e.created_at,
            "status": e.outcome,
            "reasons": e.reasons,
        }
        for e, p in db.execute(
            stmt.order_by(AutonomyEvaluation.created_at.desc(), AutonomyEvaluation.id.desc()).limit(limit)
        )
    ]
