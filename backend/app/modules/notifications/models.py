"""Tables owned by notifications. `outbox_messages` is exempt from row-level security (claimed across businesses)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id
from app.platform.models import Base, BusinessScopedBase, TimestampMixin

OUTBOX_STATUSES = ("pending", "sending", "delivered", "failed", "unknown")


class OutboxMessage(TimestampMixin, Base):
    __tablename__ = "outbox_messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"))
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str | None] = mapped_column(String(320))
    template_key: Mapped[str] = mapped_column(String(80))
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    related_type: Mapped[str | None] = mapped_column(String(40))
    related_id: Mapped[uuid.UUID | None]
    proposal_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    restricted_at: Mapped[datetime | None]
    locked_until: Mapped[datetime | None]
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None]
    resolved_by: Mapped[uuid.UUID | None]
    resolution: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (
        CheckConstraint(f"status in {OUTBOX_STATUSES}", name="status"),
        CheckConstraint("channel in ('web_chat','email','sms','simulated')", name="channel"),
        Index("ix_outbox_business_status", "business_id", "status"),
        Index("ix_outbox_related", "business_id", "related_type", "related_id"),
    )


class DeliveryAttempt(BusinessScopedBase):
    __tablename__ = "delivery_attempts"
    outbox_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outbox_messages.id"))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    result: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)


class InAppNotification(BusinessScopedBase):
    __tablename__ = "in_app_notifications"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(1000), default="")
    link: Mapped[str | None] = mapped_column(String(300))
    read_at: Mapped[datetime | None]
    __table_args__ = (Index("ix_in_app_user_unread", "business_id", "user_id", "read_at"),)
