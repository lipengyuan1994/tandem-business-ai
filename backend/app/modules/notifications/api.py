"""/api/v1/notifications: in-app notifications (own) and the outbox."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require, require_session

from . import outbound, service
from .schemas import NotificationList, NotificationOut, OutboxOut, ResolveIn, SimulatedEmail

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


@router.get(
    "/simulated-emails", response_model=list[SimulatedEmail], summary="Simulated e-mails from the last 24 hours"
)
def simulated_emails(ctx: Context = Depends(require("crm.customer.read"))) -> list[SimulatedEmail]:
    # The minimal demo mailbox exposes sign-in capabilities: owners/admins only.
    ctx.require("org.settings.write")
    from datetime import timedelta

    from sqlalchemy import select

    from app.platform import audit, clock

    from .models import OutboxMessage
    from .templates import render

    with transaction(ctx) as db:
        rows = db.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.business_id == ctx.business_id,
                OutboxMessage.channel == "simulated",
                OutboxMessage.restricted_at.is_(None),
                OutboxMessage.created_at >= clock.now() - timedelta(hours=24),
                OutboxMessage.created_at <= clock.now(),
            )
            .order_by(OutboxMessage.created_at.desc())
            .limit(100)
        )
        result = []
        for row in rows:
            if not row.variables or row.recipient == "[erased]":
                continue
            subject, body = render(row.template_key, row.variables)
            result.append(
                SimulatedEmail(
                    id=row.id,
                    recipient=row.recipient,
                    subject=subject,
                    body=body,
                    created_at=row.created_at,
                    status=row.status,
                )
            )
        assert ctx.business_id is not None
        audit.record_view(
            db, ctx, action="notifications.simulated_emails.viewed", target_type="business", target_id=ctx.business_id
        )
        return result
