"""Business-scoped in-app notifications and the proposal-created subscriber."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.identity import public as identity
from app.platform import clock
from app.platform.context import Context
from app.platform.registry import get_registry

from .models import InAppNotification


# ---------------------------------------------------------------------------------------------------- in-app
def notify_users(
    db: Session,
    ctx: Context,
    user_ids: list[uuid.UUID],
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str | None = None,
) -> None:
    assert ctx.business_id is not None
    for user_id in dict.fromkeys(user_ids):
        db.add(
            InAppNotification(
                business_id=ctx.business_id, user_id=user_id, kind=kind, title=title[:200], body=body[:1000], link=link
            )
        )
    db.flush()


def on_proposal_created(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    """Tell everyone who can approve this action type (idempotent per event)."""
    spec = get_registry().executors.get(payload["action_type"])
    if spec is None:
        return
    state = approvals.proposal_notification_state(db, ctx, uuid.UUID(payload["proposal_id"]))
    if state is None:
        return
    kind = "approval_needed"
    origin = "AI proposal" if payload["origin_kind"] == "assistant" else "New proposal"
    body = "Review this proposal before it runs."
    if state["status"] != "pending_review":
        if state["decided_by_kind"] != "policy" or state["rule_level"] != "act_notify":
            return
        kind, origin = "ai_acted", "AI acted"
        body = (
            f"Approved by autonomy rule {state['rule_id']} (v{state['rule_version']}). "
            "Check the proposal for its execution result."
        )
    elif state["would_act"]:
        body += " The autonomy rule would have approved this (shadow mode)."
    if db.scalar(
        select(InAppNotification.id)
        .where(
            InAppNotification.business_id == ctx.business_id,
            InAppNotification.link == f"/approvals/{payload['proposal_id']}",
        )
        .limit(1)
    ):
        return  # already delivered for this proposal (at-least-once events)
    approvers = [u.id for u in identity.list_users_with_permission(db, ctx, spec.approve_permission)]
    notify_users(
        db,
        ctx,
        approvers,
        kind=kind,
        title=f"{origin}: {payload['title']}",
        body=body,
        link=f"/approvals/{payload['proposal_id']}",
    )


def list_mine(db: Session, ctx: Context, limit: int = 30) -> list[InAppNotification]:
    stmt = (
        select(InAppNotification)
        .where(InAppNotification.business_id == ctx.business_id, InAppNotification.user_id == ctx.user_id)
        .order_by(InAppNotification.read_at.is_not(None), InAppNotification.created_at.desc())
        .limit(min(limit, 100))
    )
    return list(db.scalars(stmt))


def unread_count(db: Session, ctx: Context) -> int:
    stmt = (
        select(func.count())
        .select_from(InAppNotification)
        .where(
            InAppNotification.business_id == ctx.business_id,
            InAppNotification.user_id == ctx.user_id,
            InAppNotification.read_at.is_(None),
        )
    )
    return int(db.scalar(stmt) or 0)


def mark_read(db: Session, ctx: Context, notification_id: uuid.UUID | None) -> int:
    stmt = update(InAppNotification).where(
        InAppNotification.business_id == ctx.business_id,
        InAppNotification.user_id == ctx.user_id,
        InAppNotification.read_at.is_(None),
    )
    if notification_id is not None:
        stmt = stmt.where(InAppNotification.id == notification_id)
    return db.execute(stmt.values(read_at=clock.now())).rowcount  # type: ignore[attr-defined, no-any-return]
