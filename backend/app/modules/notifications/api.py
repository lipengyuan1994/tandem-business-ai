"""/api/v1/notifications: in-app notifications (own) and the outbox."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require, require_session

from . import outbound, service
from .schemas import NotificationList, NotificationOut, OutboxOut, ResolveIn

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


@router.get("", response_model=NotificationList, summary="My notifications (unread first)", responses=ERRORS)
def my_notifications(ctx: Context = Depends(require_session())) -> NotificationList:
    if ctx.business_id is None:
        return NotificationList(items=[], unread=0)
    with transaction(ctx) as db:
        return NotificationList(
            items=[NotificationOut.model_validate(n) for n in service.list_mine(db, ctx)],
            unread=service.unread_count(db, ctx),
        )


@router.post("/read-all", summary="Mark all my notifications read", responses=ERRORS)
def read_all(ctx: Context = Depends(require_session())) -> dict[str, int]:
    with transaction(ctx) as db:
        return {"updated": service.mark_read(db, ctx, None)}


@router.post("/{notification_id}/read", summary="Mark one notification read", responses=ERRORS)
def read_one(notification_id: uuid.UUID, ctx: Context = Depends(require_session())) -> dict[str, int]:
    with transaction(ctx) as db:
        return {"updated": service.mark_read(db, ctx, notification_id)}


@router.get(
    "/outbox",
    response_model=list[OutboxOut],
    summary="Outbox messages (filter by status or proposal)",
    responses=ERRORS,
)
def outbox(
    status: str | None = None,
    proposal_id: uuid.UUID | None = None,
    ctx: Context = Depends(require("approvals.proposal.read")),
) -> list[OutboxOut]:
    with transaction(ctx) as db:
        return [
            OutboxOut.model_validate(m) for m in service.list_outbox(db, ctx, status=status, proposal_id=proposal_id)
        ]


@router.post(
    "/outbox/{outbox_id}/resolve", response_model=OutboxOut, summary="Resolve an uncertain delivery", responses=ERRORS
)
def resolve(
    outbox_id: uuid.UUID, body: ResolveIn, ctx: Context = Depends(require("notifications.outbox.resolve"))
) -> OutboxOut:
    with transaction(ctx) as db:
        return OutboxOut.model_validate(service.resolve(db, ctx, outbox_id, body.resolution))


@router.post(
    "/outbox/{outbox_id}/stop", response_model=OutboxOut, summary="Stop a queued outbound message", responses=ERRORS
)
def stop(outbox_id: uuid.UUID, ctx: Context = Depends(require_session())) -> OutboxOut:
    with transaction(ctx) as db:
        return OutboxOut.model_validate(outbound.stop(db, ctx, outbox_id))
