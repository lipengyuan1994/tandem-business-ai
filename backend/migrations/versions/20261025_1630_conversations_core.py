"""conversations: conversations, messages, visitor tokens

Revision ID: 20261025_1630_conversations_core
Revises: 20261025_1620_inventory_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261025_1630_conversations_core"
down_revision = "20261025_1620_inventory_core"
branch_labels = None
depends_on = None

U, TS = UUID(as_uuid=True), sa.DateTime(timezone=True)
TABLES = ("conversations", "messages", "visitor_tokens")


def upgrade() -> None:
    op.create_table(
        "conversations", *scoped_columns(), sa.Column("customer_id", U), sa.Column("channel", sa.String(12), nullable=False),
        sa.Column("subject", sa.String(200)), sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("assigned_to", U), sa.Column("last_message_at", TS, nullable=False), sa.Column("visitor_name", sa.String(200)),
        sa.Column("visitor_email", sa.String(320)), sa.Column("visitor_phone", sa.String(40)), sa.Column("handoff_requested_at", TS),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"),
        sa.CheckConstraint("channel in ('web_chat','phone_log','walk_in','email','voice')", name="channel"),
        sa.CheckConstraint("status in ('open','waiting_on_staff','waiting_on_customer','closed')", name="status"),
    )
    op.create_index("ix_conversations_inbox", "conversations", ["business_id", "status", "last_message_at"])
    op.create_table(
        "messages", *scoped_columns(), sa.Column("conversation_id", U, nullable=False), sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("author_kind", sa.String(10), nullable=False), sa.Column("author_user_id", U), sa.Column("body", sa.Text, nullable=False),
        sa.Column("delivery_status", sa.String(12)), sa.Column("outbox_message_id", U), sa.Column("source_ref", sa.String(200)),
        sa.Column("proposal_id", U), sa.UniqueConstraint("business_id", "id"), composite_fk("conversation_id", "conversations"),
        sa.CheckConstraint("direction in ('inbound','outbound','internal')", name="direction"),
        sa.CheckConstraint("author_kind in ('visitor','customer','staff','assistant','system')", name="author_kind"),
        sa.CheckConstraint("length(body) <= 8000", name="body_length"),
    )
    op.create_index("uq_messages_source_ref", "messages", ["business_id", "source_ref"], unique=True, postgresql_where=sa.text("source_ref is not null"))
    op.create_index("ix_messages_thread", "messages", ["business_id", "conversation_id", "created_at"])
    op.create_table(
        "visitor_tokens", *scoped_columns(), sa.Column("conversation_id", U, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS), sa.UniqueConstraint("business_id", "id"), composite_fk("conversation_id", "conversations"),
    )
    for table in TABLES:
        scoped_table(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
