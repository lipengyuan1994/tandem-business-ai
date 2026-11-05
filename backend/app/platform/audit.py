"""Audit writer (07-workflow-engine.md#audit). Written in the caller's transaction; never commits."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.canonical import to_jsonable
from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.errors import Forbidden
from app.platform.ids import new_id
from app.platform.models import AuditEvent

REDACTED = "[redacted]"


def record(
    db: Session,
    ctx: Context,
    *,
    action: str,
    target_type: str,
    target_id: UUID | None,
    summary: str,
    changes: dict[str, Any] | None = None,
    proposal_id: UUID | None = None,
) -> UUID:
    if ctx.business_id is None:
        raise Forbidden("auth.no_active_business", detail="audit requires a business")
    if ctx.autonomy_rule_id is not None:
        marker = (
            f"autonomy rule {ctx.autonomy_rule_id} v{ctx.autonomy_rule_version}, granted by {ctx.on_behalf_of_user_id}"
        )
        summary = f"{marker}: {summary}"
    event_id = new_id()
    actor_id = ctx.user_id
    db.execute(
        insert(AuditEvent).values(
            id=event_id,
            occurred_at=clock.now(),
            business_id=ctx.business_id,
            actor_kind=ctx.actor_kind,
            actor_id=actor_id,
            on_behalf_of_user_id=ctx.on_behalf_of_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            proposal_id=proposal_id or ctx.proposal_id,
            ai_run_id=ctx.ai_run_id,
            request_id=ctx.request_id,
            summary=summary[:500],
            changes=to_jsonable(changes or {}),
        )
    )
    return event_id


def _value(obj: Any, field: str) -> Any:
    return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)


def diff(before: Any, after: Any, fields: Iterable[str], redact: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    """{'field': {'from': x, 'to': y}} for fields that changed; redacted fields show '[redacted]'."""
    hidden = set(redact)
    out: dict[str, dict[str, Any]] = {}
    for field in fields:
        old, new = _value(before, field), _value(after, field)
        if old != new:
            out[field] = {"from": REDACTED, "to": REDACTED} if field in hidden else {"from": old, "to": new}
    return out


def record_view(db: Session, ctx: Context, *, action: str, target_type: str, target_id: UUID) -> None:
    """Regulated staff reads only. No database work in standard; serialize concurrent reads before checking."""
    if not is_regulated() or ctx.user_id is None:
        return
    key = f"view:{ctx.business_id}:{ctx.user_id}:{target_type}:{target_id}"
    db.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    last = db.scalar(
        select(AuditEvent.occurred_at)
        .where(
            AuditEvent.business_id == ctx.business_id,
            AuditEvent.actor_id == ctx.user_id,
            AuditEvent.action == action,
            AuditEvent.target_type == target_type,
            AuditEvent.target_id == target_id,
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(1)
    )
    if last is None or last <= clock.now() - timedelta(minutes=10):
        record(db, ctx, action=action, target_type=target_type, target_id=target_id, summary="Record viewed")
