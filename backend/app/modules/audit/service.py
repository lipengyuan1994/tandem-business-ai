"""Read-only audit browsing and CSV export (audit events are written by app.platform.audit)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Iterator

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.platform import audit
from app.platform.context import Context
from app.platform.errors import NotFound
from app.platform.models import AuditEvent
from app.platform.pagination import clamp_limit, decode_cursor, encode_cursor

from . import permissions as P
from .schemas import AuditEventOut, AuditFilter, AuditPage

EXPORT_MAX_ROWS = 100_000


def _query(ctx: Context, f: AuditFilter) -> Select[AuditEvent]:
    stmt = select(AuditEvent).where(AuditEvent.business_id == ctx.business_id)
    if f.occurred_from:
        stmt = stmt.where(AuditEvent.occurred_at >= f.occurred_from)
    if f.occurred_to:
        stmt = stmt.where(AuditEvent.occurred_at < f.occurred_to)
    if f.actor_id:
        stmt = stmt.where(or_(AuditEvent.actor_id == f.actor_id, AuditEvent.on_behalf_of_user_id == f.actor_id))
    if f.actor_kind:
        stmt = stmt.where(AuditEvent.actor_kind == f.actor_kind)
    if f.action_prefix:
        stmt = stmt.where(AuditEvent.action.startswith(f.action_prefix, autoescape=True))
    if f.target_type:
        stmt = stmt.where(AuditEvent.target_type == f.target_type)
    if f.target_id:
        stmt = stmt.where(AuditEvent.target_id == f.target_id)
    if f.proposal_id:
        stmt = stmt.where(AuditEvent.proposal_id == f.proposal_id)
    return stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())


def _actor_label(row: AuditEvent, names: dict[uuid.UUID, str]) -> str:
    if row.actor_kind == "user" and row.actor_id:
        return names.get(row.actor_id, "Unknown user")
    if row.actor_kind == "assistant":
        who = names.get(row.on_behalf_of_user_id) if row.on_behalf_of_user_id else None
        return f"Assistant (on behalf of {who})" if who else "Assistant (intake)"
    return {"system": "System", "visitor": "Website visitor"}.get(row.actor_kind, row.actor_kind)


def _to_out(db: Session, ctx: Context, rows: list[AuditEvent]) -> list[AuditEventOut]:
    ids = {r.actor_id for r in rows if r.actor_id} | {r.on_behalf_of_user_id for r in rows if r.on_behalf_of_user_id}
    names = {k: v.display_name for k, v in identity.get_user_briefs(db, ctx, ids).items()}
    return [
        AuditEventOut(
            id=r.id,
            occurred_at=r.occurred_at,
            actor_kind=r.actor_kind,
            actor_id=r.actor_id,
            actor_name=_actor_label(r, names),
            on_behalf_of_user_id=r.on_behalf_of_user_id,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            proposal_id=r.proposal_id,
            ai_run_id=r.ai_run_id,
            request_id=r.request_id,
            summary=r.summary,
            changes=r.changes,
        )
        for r in rows
    ]


def list_events(db: Session, ctx: Context, f: AuditFilter, limit: int = 50, cursor: str | None = None) -> AuditPage:
    ctx.require(P.EVENT_READ)
    limit = clamp_limit(limit)
    stmt = _query(ctx, f)
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(or_(AuditEvent.occurred_at < ts, and_(AuditEvent.occurred_at == ts, AuditEvent.id < rid)))
    rows = list(db.scalars(stmt.limit(limit + 1)))
    page, more = rows[:limit], len(rows) > limit
    return AuditPage(
        items=_to_out(db, ctx, page),
        next_cursor=encode_cursor(page[-1].occurred_at, page[-1].id) if more and page else None,
    )


def get_event(db: Session, ctx: Context, event_id: object) -> AuditEventOut:
    ctx.require(P.EVENT_READ)
    row = db.scalar(select(AuditEvent).where(AuditEvent.business_id == ctx.business_id, AuditEvent.id == event_id))
    if row is None:
        raise NotFound("audit.event_not_found")
    return _to_out(db, ctx, [row])[0]


def export_csv(db: Session, ctx: Context, f: AuditFilter) -> Iterator[str]:
    """Rows are built inside the caller's transaction; the export itself is audited."""
    ctx.require(P.EXPORT)
    rows = list(db.scalars(_query(ctx, f).limit(EXPORT_MAX_ROWS)))
    events = _to_out(db, ctx, rows)
    audit.record(
        db,
        ctx,
        action="audit.exported",
        target_type="audit_log",
        target_id=None,
        summary=f"Audit log exported ({len(events)} rows)",
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "occurred_at",
            "actor",
            "actor_kind",
            "action",
            "target_type",
            "target_id",
            "proposal_id",
            "summary",
            "changes",
        ]
    )
    lines = [buffer.getvalue()]
    for e in events:
        buffer.seek(0)
        buffer.truncate()
        writer.writerow(
            [
                _csv_text(str(value))
                for value in [
                    e.occurred_at.isoformat(),
                    e.actor_name,
                    e.actor_kind,
                    e.action,
                    e.target_type,
                    e.target_id or "",
                    e.proposal_id or "",
                    e.summary,
                    json.dumps(e.changes, sort_keys=True),
                ]
            ]
        )
        lines.append(buffer.getvalue())
    return iter(lines)


def _csv_text(value: str) -> str:
    return (
        "'" + value
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
        else value
    )
