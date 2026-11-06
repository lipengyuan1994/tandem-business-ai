"""Unified metadata timeline over existing rows. Every queue/event query explicitly scopes the tenant."""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.platform.context import Context
from app.platform.errors import NotFound
from app.platform.models import DomainEvent, Job
from app.platform.pagination import decode_cursor, encode_cursor

from .agents import JOB_OWNERS, USE_CASE_OWNERS, get_agent
from .models import AiRun
from .team_models import ChainBudget
from .team_schemas import ChainOut, TeamActivityItem, TeamActivityPage


def _before(stmt: Any, at: Any, ident: Any, cursor: str | None, limit: int | None) -> Any:
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(or_(at < ts, and_(at == ts, ident < rid)))
    stmt = stmt.order_by(at.desc(), ident.desc())
    return stmt.limit(limit) if limit else stmt


def _runs(
    db: Session, ctx: Context, key: str | None, correlation_id: UUID | None, cursor: str | None, limit: int | None
) -> list[TeamActivityItem]:
    stmt = select(AiRun).where(AiRun.business_id == ctx.business_id)
    if key:
        stmt = stmt.where(
            or_(AiRun.agent_key == key, and_(AiRun.agent_key.is_(None), AiRun.use_case.in_(get_agent(key).use_cases)))
        )
    if correlation_id:
        stmt = stmt.where(AiRun.correlation_id == correlation_id)
    return [
        TeamActivityItem(
            id=r.id,
            agent=r.agent_key or USE_CASE_OWNERS.get(r.use_case),
            kind="run",
            created_at=r.started_at,
            status=r.status,
            title=f"{r.use_case} run",
            run_id=r.id,
            correlation_id=r.correlation_id,
            causation_id=r.causation_id,
            cost_micros=r.cost_micros,
            reasons=[r.error_code] if r.error_code else [],
            result_link=f"/api/v1/ai/runs?use_case={r.use_case}",
        )
        for r in db.scalars(_before(stmt, AiRun.started_at, AiRun.id, cursor, limit))
    ]


def timeline(
    db: Session,
    ctx: Context,
    *,
    key: str | None = None,
    correlation_id: UUID | None = None,
    cursor: str | None = None,
    limit: int | None = 51,
) -> list[TeamActivityItem]:
    ctx.require("approvals.proposal.read")
    agent = get_agent(key) if key else None
    rows = _runs(db, ctx, key, correlation_id, cursor, limit)
    rows += [
        TeamActivityItem.model_validate(row)
        for row in approvals.team_activity(
            db,
            ctx,
            key=key,
            action_types=agent.action_types if agent else (),
            correlation_id=correlation_id,
            cursor=cursor,
            limit=limit,
        )
    ]
    stmt = select(DomainEvent).where(DomainEvent.business_id == ctx.business_id)
    if key:
        stmt = stmt.where(DomainEvent.agent_key == key)
    if correlation_id:
        stmt = stmt.where(DomainEvent.correlation_id == correlation_id)
    # Agent pages include delivery evidence; chain views include all emitted domain events.
    if not correlation_id:
        stmt = stmt.where(DomainEvent.type.in_(("notifications.delivery_status_changed", "ai.customer_care_activity")))
    for event in db.scalars(_before(stmt, DomainEvent.occurred_at, DomainEvent.id, cursor, limit)):
        delivery = event.type == "notifications.delivery_status_changed"
        status = (
            str(event.payload.get("status", "occurred"))
            if delivery or event.type == "ai.customer_care_activity"
            else "occurred"
        )
        mapped = {"pending": "scheduled", "cancelled": "stopped"}.get(status, status)
        outbox = event.payload.get("outbox_message_id") if delivery else None
        try:
            outbox_id = UUID(str(outbox)) if outbox else None
        except ValueError:
            outbox_id = None
        rows.append(
            TeamActivityItem(
                id=event.id,
                agent=event.agent_key,
                kind="delivery" if delivery else "event",
                created_at=event.occurred_at,
                status=status,
                title="Outbound delivery" if delivery else event.type,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                proposal_id=event.causation_id if delivery else None,
                result_link=f"/api/v1/notifications/outbox?proposal_id={event.causation_id}" if outbox_id else None,
                reasons=[str(event.payload["reason"])]
                if event.type == "ai.customer_care_activity" and event.payload.get("reason")
                else [],
                delivery_state=mapped
                if mapped in {"scheduled", "sending", "delivered", "failed", "unknown", "stopped"}
                else "none",
            )
        )
    if correlation_id:
        jobs_stmt = select(Job).where(Job.business_id == ctx.business_id, Job.correlation_id == correlation_id)
        for job in db.scalars(_before(jobs_stmt, Job.created_at, Job.id, cursor, limit)):
            rows.append(
                TeamActivityItem(
                    id=job.id,
                    agent=JOB_OWNERS.get(job.kind, job.agent_key),
                    kind="job",
                    created_at=job.created_at,
                    status=job.status,
                    title=job.kind,
                    correlation_id=job.correlation_id,
                    causation_id=job.causation_id,
                )
            )
    return sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)


def activity(db: Session, ctx: Context, key: str, *, cursor: str | None = None, limit: int = 50) -> TeamActivityPage:
    rows = timeline(db, ctx, key=key, cursor=cursor, limit=limit + 1)
    selected = rows[:limit]
    return TeamActivityPage(
        items=selected,
        next_cursor=encode_cursor(selected[-1].created_at, selected[-1].id) if len(rows) > limit else None,
    )


def chain(db: Session, ctx: Context, correlation_id: UUID) -> ChainOut:
    rows = timeline(db, ctx, correlation_id=correlation_id, limit=None)
    counter = db.scalar(
        select(ChainBudget).where(
            ChainBudget.business_id == ctx.business_id, ChainBudget.correlation_id == correlation_id
        )
    )
    if not rows and counter is None:
        raise NotFound("ai.chain_not_found")
    # Causality wins over timestamp ties (e.g. a frozen clock, or server/client clock skew).
    by_id = {r.id: r for r in rows}
    ordered: list[TeamActivityItem] = []
    visited: set[UUID] = set()

    def visit(row: TeamActivityItem) -> None:
        if row.id in visited:
            return
        visited.add(row.id)
        parent = by_id.get(row.causation_id) if row.causation_id else None
        if parent is not None:
            visit(parent)
        ordered.append(row)

    for row in reversed(rows):
        visit(row)
    return ChainOut(
        correlation_id=correlation_id,
        model_calls=counter.model_calls if counter else 0,
        actions=counter.actions if counter else 0,
        cost_micros=counter.cost_micros if counter else 0,
        stopped_reason=counter.stopped_reason if counter else None,
        steps=ordered,
    )


def exception_items(db: Session, ctx: Context, *, limit: int, cursor: str | None = None) -> list[dict[str, Any]]:
    stmt = select(AiRun).where(
        AiRun.business_id == ctx.business_id,
        or_(AiRun.error_code.startswith("ai.chain_"), AiRun.error_code == "ai.agent_limit_reached"),
    )
    return [
        {
            "id": r.id,
            "kind": "chain_blocked",
            "title": f"{r.agent_key}: {r.error_code}",
            "created_at": r.started_at,
            "status": r.status,
            "failure_code": r.error_code,
            "reasons": [r.error_code],
        }
        for r in db.scalars(_before(stmt, AiRun.started_at, AiRun.id, cursor, limit))
    ]
