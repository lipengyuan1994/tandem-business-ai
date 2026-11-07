"""Public API of the notifications module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.platform.context import Context

from .events import DeliveryStatusChanged
from .outbound import queue_items as outbound_queue_items
from .service import email_channel, enqueue_message, notify_users, simulated_delivery, unread_count
from .service import status_counts as outbox_status_counts

__all__ = [
    "DeliveryStatusChanged",
    "current_delivery_status",
    "email_channel",
    "enqueue_message",
    "notify_users",
    "outbound_queue_items",
    "outbox_status_counts",
    "simulated_delivery",
    "unread_count",
]


def current_delivery_status(db: Session, ctx: Context, outbox_id: uuid.UUID) -> str | None:
    """Read authoritative state under a lock held until the consumer's projection commits."""
    from sqlalchemy import select

    from .models import OutboxMessage

    return db.scalar(
        select(OutboxMessage.status)
        .where(
            OutboxMessage.business_id == ctx.business_id,
            OutboxMessage.id == outbox_id,
        )
        .with_for_update(read=True)
    )


def notify_permission(
    db: Session, ctx: Context, permission: str, *, kind: str, title: str, body: str, link: str
) -> None:
    """Notify active staff with a current permission without exposing identity internals to callers."""
    from app.modules.identity import public as identity

    notify_users(
        db,
        ctx,
        [u.id for u in identity.list_users_with_permission(db, ctx, permission)],
        kind=kind,
        title=title,
        body=body,
        link=link,
    )
