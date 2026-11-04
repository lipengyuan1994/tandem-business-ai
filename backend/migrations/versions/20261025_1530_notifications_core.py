"""notifications: outbox (RLS-exempt queue), delivery attempts, in-app notifications

Revision ID: 20261025_1530_notifications_core
Revises: 20261025_1500_approvals_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import append_only, create_updated_at_trigger, disable_business_rls, enable_business_rls

revision = "20261025_1530_notifications_core"
down_revision = "20261025_1500_approvals_core"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(320)),
        sa.Column("template_key", sa.String(80), nullable=False),
        sa.Column("variables", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("related_type", sa.String(40)),
        sa.Column("related_id", UUID(as_uuid=True)),
        sa.Column("proposal_id", UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(200)),
        sa.Column("last_error", sa.Text),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", UUID(as_uuid=True)),
        sa.Column("resolution", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status in ('pending','sending','delivered','failed','unknown')", name="status"),
        sa.CheckConstraint("channel in ('web_chat','email','sms','simulated')", name="channel"),
    )
    op.create_index("ix_outbox_business_status", "outbox_messages", ["business_id", "status"])
    op.create_index("ix_outbox_related", "outbox_messages", ["business_id", "related_type", "related_id"])
    create_updated_at_trigger("outbox_messages")
    op.create_table(
        "delivery_attempts",
        *_base(),
        sa.Column("outbox_message_id", UUID(as_uuid=True), sa.ForeignKey("outbox_messages.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("error", sa.Text),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_table(
        "in_app_notifications",
        *_base(),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(1000), nullable=False, server_default=""),
        sa.Column("link", sa.String(300)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"),
    )
    op.create_index("ix_in_app_user_unread", "in_app_notifications", ["business_id", "user_id", "read_at"])
    for table in ("delivery_attempts", "in_app_notifications"):
        enable_business_rls(table)
        create_updated_at_trigger(table)
    append_only("delivery_attempts")


def downgrade() -> None:
    for table in ("in_app_notifications", "delivery_attempts"):
        disable_business_rls(table)
        op.drop_table(table)
    op.drop_table("outbox_messages")
