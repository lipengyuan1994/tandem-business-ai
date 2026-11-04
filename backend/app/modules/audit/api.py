"""/api/v1/audit: browse and export."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require

from . import permissions as P
from . import service
from .schemas import AuditEventOut, AuditFilter, AuditPage

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 422)


def _filter(
    occurred_from: datetime | None = Query(None, alias="from"),
    occurred_to: datetime | None = Query(None, alias="to"),
    actor_id: uuid.UUID | None = None,
    actor_kind: str | None = None,
    action_prefix: str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
) -> AuditFilter:
    return AuditFilter(
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        actor_id=actor_id,
        actor_kind=actor_kind,
        action_prefix=action_prefix,
        target_type=target_type,
        target_id=target_id,
        proposal_id=proposal_id,
    )


@router.get("/events", response_model=AuditPage, summary="Audit events, newest first", responses=ERRORS)
def list_events(
    f: AuditFilter = Depends(_filter),
    limit: int = 50,
    cursor: str | None = None,
    ctx: Context = Depends(require(P.EVENT_READ)),
) -> AuditPage:
    with transaction(ctx) as db:
        return service.list_events(db, ctx, f, limit, cursor)


@router.get("/events/{event_id}", response_model=AuditEventOut, summary="One audit event", responses=ERRORS)
def get_event(event_id: uuid.UUID, ctx: Context = Depends(require(P.EVENT_READ))) -> AuditEventOut:
    with transaction(ctx) as db:
        return service.get_event(db, ctx, event_id)


@router.get(
    "/export.csv",
    summary="Export audit events as CSV (max 100,000 rows)",
    response_class=StreamingResponse,
    responses=ERRORS,
)
def export_csv(f: AuditFilter = Depends(_filter), ctx: Context = Depends(require(P.EXPORT))) -> StreamingResponse:
    with transaction(ctx) as db:
        lines = service.export_csv(db, ctx, f)
    return StreamingResponse(
        lines, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit.csv"'}
    )
