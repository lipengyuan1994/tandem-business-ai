"""Read-only unified queues over existing public module APIs; no second workflow engine."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.modules.approvals import public as approvals
from app.modules.conversations import public as conversations
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.modules.purchasing import public as purchasing
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Invalid, error_responses
from app.platform.pagination import encode_cursor
from app.platform.permissions import require

router = APIRouter()


class WorkItem(BaseModel):
    id: uuid.UUID
    kind: Literal[
        "emergency_handoff",
        "proposal",
        "outbound_mail",
        "supplier_quote",
        "autonomy_fallback",
        "web_reply",
        "chain_blocked",
        "customer_care_case",
    ]
    title: str
    created_at: datetime
    status: str
    proposal_id: uuid.UUID | None = None
    action_type: str | None = None
    approve_permission: str | None = None
    waiting_on_roles: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    stop_until: datetime | None = None
    due_date: date | None = None
    quote_request_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    priority: str | None = None
    category: Literal["complaint", "warranty", "dispute"] | None = None
    version: int | None = None
    customer_name: str | None = None
    customer_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    appointment_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    failure_code: str | None = None
    reasons: list[str] = Field(default_factory=list)


class WorkPage(BaseModel):
    items: list[WorkItem]
    next_cursor: str | None


class ExceptionPage(WorkPage):
    paused: bool


def page(rows: list[dict[str, Any]], limit: int) -> WorkPage:
    items = sorted((WorkItem.model_validate(row) for row in rows), key=lambda r: (r.created_at, r.id), reverse=True)
    selected = items[:limit]
    return WorkPage(
        items=selected,
        next_cursor=encode_cursor(selected[-1].created_at, selected[-1].id) if len(items) > limit else None,
    )


@router.get(
    "/work-queue",
    response_model=WorkPage,
    summary="Emergency hand-overs, priority Care cases, then newest ordinary work",
    responses=error_responses(400, 401, 403, 422),
)
def work_queue(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    ctx: Context = Depends(require("approvals.proposal.read")),
) -> WorkPage:
    with transaction(ctx) as db:
        from .customer_care.api import queue_cursor
        from .customer_care.api import queue_items as care_items

        band, token = "emergency", None
        if cursor:
            prefix, _, value = cursor.partition(":")
            if prefix in {"emergency", "care", "ordinary"} and not value:
                raise Invalid("platform.invalid_cursor")
            # Retain pre-band chronological cursors as ordinary continuations.
            band, token = (prefix, value) if prefix in {"emergency", "care", "ordinary"} else ("ordinary", cursor)
        selected: list[WorkItem] = []
        continuations: list[str] = []

        def append(rows: list[dict[str, Any]], prefix: str) -> None:
            for row in rows:
                item = WorkItem.model_validate(row)
                selected.append(item)
                keyset = (
                    queue_cursor(item.priority or "normal", item.created_at, item.id)
                    if prefix == "care"
                    else encode_cursor(item.created_at, item.id)
                )
                continuations.append(prefix + ":" + keyset)

        if band == "emergency":
            append(conversations.emergency_queue_items(db, ctx, limit=limit + 1, cursor=token), "emergency")
            band, token = "care", None
        if band == "care" and len(selected) <= limit:
            append(care_items(db, ctx, limit=limit + 1 - len(selected), cursor=token), "care")
            band, token = "ordinary", None
        if band == "ordinary" and len(selected) <= limit:
            remaining = limit + 1 - len(selected)
            rows = approvals.proposal_queue_items(db, ctx, limit=remaining, cursor=token)
            rows += notifications.outbound_queue_items(db, ctx, statuses=("pending",), limit=remaining, cursor=token)
            rows += purchasing.awaiting_supplier_items(db, ctx, limit=remaining, cursor=token)
            rows += conversations.publication_queue_items(db, ctx, limit=remaining, cursor=token)
            ordered = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)
            append(ordered[:remaining], "ordinary")
        return WorkPage(
            items=selected[:limit],
            next_cursor=continuations[limit - 1] if len(selected) > limit else None,
        )


@router.get(
    "/exceptions",
    response_model=ExceptionPage,
    summary="Execution, delivery and recent autonomy exceptions",
    responses=error_responses(400, 401, 403, 422),
)
def exceptions(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    ctx: Context = Depends(require("approvals.proposal.read")),
) -> ExceptionPage:
    with transaction(ctx) as db:
        rows = approvals.proposal_queue_items(db, ctx, failed=True, limit=limit + 1, cursor=cursor)
        rows += approvals.fallback_queue_items(db, ctx, limit=limit + 1, cursor=cursor)
        rows += approvals.team_return_items(db, ctx, limit=limit + 1, cursor=cursor)
        from .team_activity import exception_items

        rows += exception_items(db, ctx, limit=limit + 1, cursor=cursor)
        rows += conversations.publication_queue_items(db, ctx, failed=True, limit=limit + 1, cursor=cursor)
        rows += notifications.outbound_queue_items(
            db, ctx, statuses=("failed", "unknown"), limit=limit + 1, cursor=cursor
        )
        return ExceptionPage(**page(rows, limit).model_dump(), paused=org.autonomy_paused(db, ctx))
