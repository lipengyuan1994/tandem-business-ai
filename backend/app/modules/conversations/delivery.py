"""Project the authoritative outbox status into conversation messages."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notifications import public as notifications
from app.platform.context import Context

from .models import Message


def on_delivery_status(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    if payload.get("related_type") != "message" or not payload.get("related_id"):
        return
    outbox_id = uuid.UUID(payload["outbox_message_id"])
    # Match erasure's message -> outbox order, then keep authoritative state stable until projection commits.
    message = db.scalar(
        select(Message)
        .where(
            Message.business_id == ctx.business_id,
            Message.id == uuid.UUID(payload["related_id"]),
            Message.outbox_message_id == outbox_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if message is None:
        return
    status = notifications.current_delivery_status(db, ctx, outbox_id)
    if status is not None:
        message.delivery_status = status
