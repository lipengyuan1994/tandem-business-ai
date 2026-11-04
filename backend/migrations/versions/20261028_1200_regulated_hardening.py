"""Durable delivery/conversation restrictions and authentication origin (P8-23 review).

Revision ID: 20261028_1200_regulated_hardening
Revises: 20261028_1100_regulated_profile
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261028_1200_regulated_hardening"
down_revision = "20261028_1100_regulated_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.add_column("outbox_messages", sa.Column("restricted_at", ts))
    op.add_column("conversations", sa.Column("restricted_at", ts))
    op.add_column("sessions", sa.Column("authenticated_at", ts, nullable=True))
    op.execute("update sessions set authenticated_at = created_at")
    op.alter_column("sessions", "authenticated_at", nullable=False, server_default=sa.func.now())
    op.execute("""update conversations c set restricted_at = u.updated_at from customers u
        where c.business_id = u.business_id and c.customer_id = u.id and u.status = 'restricted'""")
    op.execute("""update outbox_messages o set restricted_at = now()
        where o.last_error = 'customer restricted; delivery blocked'
        or exists (select 1 from conversations c where c.business_id = o.business_id
            and c.id = o.related_id and c.restricted_at is not null)
        or exists (select 1 from messages m join conversations c
            on c.business_id = m.business_id and c.id = m.conversation_id
            where m.business_id = o.business_id and c.restricted_at is not null
                and (m.id = o.related_id or m.outbox_message_id = o.id))
        or exists (select 1 from appointments a join customers c
            on c.business_id = a.business_id and c.id = a.customer_id
            where a.business_id = o.business_id and a.id = o.related_id and c.status = 'restricted')
        or exists (select 1 from customers c where c.business_id = o.business_id
            and c.id = o.related_id and c.status = 'restricted')""")

    op.execute("""update outbox_messages set status = 'failed', last_error = 'customer restricted; delivery blocked'
        where restricted_at is not null and status in ('pending','sending','unknown')""")
    op.execute("""delete from idempotency_keys i using customers c
        where c.business_id = i.business_id and c.status = 'restricted'
            and i.response_body::text like '%' || c.id::text || '%'""")


def downgrade() -> None:
    # Dropping these markers would release blocks or extend authentication without an explicit data decision.
    op.execute("""do $$ begin
        if exists (select 1 from outbox_messages where restricted_at is not null)
           or exists (select 1 from conversations where restricted_at is not null)
           or exists (select 1 from sessions where authenticated_at < created_at) then
            raise exception 'cannot discard durable restrictions or authentication origin';
        end if;
    end $$""")
    op.drop_column("sessions", "authenticated_at")
    op.drop_column("conversations", "restricted_at")
    op.drop_column("outbox_messages", "restricted_at")
