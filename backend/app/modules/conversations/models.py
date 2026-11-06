from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


class Conversation(BusinessScopedBase):
    __tablename__ = "conversations"
    customer_id: Mapped[uuid.UUID | None]
    channel: Mapped[str] = mapped_column(String(12))
    subject: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="open")
    assigned_to: Mapped[uuid.UUID | None]
    last_message_at: Mapped[datetime]
    visitor_name: Mapped[str | None] = mapped_column(String(200))
    visitor_email: Mapped[str | None] = mapped_column(String(320))
    visitor_phone: Mapped[str | None] = mapped_column(String(40))
    contact_consent_at: Mapped[datetime | None]
    contact_consent_text: Mapped[str | None] = mapped_column(Text)
    restricted_at: Mapped[datetime | None]
    erased_at: Mapped[datetime | None]
    handoff_requested_at: Mapped[datetime | None]
    ai_mode: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")
    ai_generation: Mapped[int] = mapped_column(default=0, server_default="0")
    ai_paused_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        CheckConstraint(
            "ai_generation >= 0 and ((ai_mode='auto' and ai_paused_at is null) or "
            "(ai_mode='human' and ai_paused_at is not null))",
            name="ai_ownership",
        ),
        ForeignKeyConstraint(["business_id", "customer_id"], ["customers.business_id", "customers.id"]),
        CheckConstraint("channel in ('web_chat','phone_log','walk_in','email','voice')", name="channel"),
        CheckConstraint("status in ('open','waiting_on_staff','waiting_on_customer','closed')", name="status"),
        CheckConstraint(
            "(contact_consent_at is null) = (contact_consent_text is null)", name="contact_consent_evidence"
        ),
        Index("ix_conversations_inbox", "business_id", "status", "last_message_at"),
    )


class Message(BusinessScopedBase):
    __tablename__ = "messages"
    conversation_id: Mapped[uuid.UUID]
    direction: Mapped[str] = mapped_column(String(10))
    author_kind: Mapped[str] = mapped_column(String(10))
    author_user_id: Mapped[uuid.UUID | None]
    body: Mapped[str] = mapped_column(Text)
    delivery_status: Mapped[str | None] = mapped_column(String(12))
    outbox_message_id: Mapped[uuid.UUID | None]
    source_ref: Mapped[str | None] = mapped_column(String(200))
    proposal_id: Mapped[uuid.UUID | None]
    ai_generation: Mapped[int] = mapped_column(default=0, server_default="0")
    publication_state: Mapped[str] = mapped_column(String(12), default="published", server_default="published")
    publish_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]
    answers_message_id: Mapped[uuid.UUID | None]
    cancellation_reason: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "conversation_id"], ["conversations.business_id", "conversations.id"]),
        CheckConstraint("direction in ('inbound','outbound','internal')", name="direction"),
        CheckConstraint("author_kind in ('visitor','customer','staff','assistant','system')", name="author_kind"),
        CheckConstraint("length(body) <= 8000", name="body_length"),
        CheckConstraint(
            "publication_state in ('withheld','published','stopped','cancelled')", name="publication_state"
        ),
        CheckConstraint(
            "publication_state != 'withheld' or (publish_at is not null and proposal_id is not null)",
            name="publication_due",
        ),
        ForeignKeyConstraint(["business_id", "answers_message_id"], ["messages.business_id", "messages.id"]),
        Index(
            "uq_messages_source_ref",
            "business_id",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref is not null"),
        ),
        Index("ix_messages_thread", "business_id", "conversation_id", "created_at"),
        Index(
            "ix_messages_publication",
            "business_id",
            "publish_at",
            postgresql_where=text("publication_state='withheld'"),
        ),
    )


class VisitorToken(BusinessScopedBase):
    __tablename__ = "visitor_tokens"
    conversation_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "conversation_id"], ["conversations.business_id", "conversations.id"]),
    )
