"""Indexed bounded platform retention and permanent job deduplication receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261104_0700_retention"
down_revision = "20261104_0600_ai_budgets"
branch_labels = None
depends_on = None

INDEXES = (
    ("jobs", "ix_jobs_retention", ["updated_at"], "status in ('succeeded','dead','failed')"),
    ("domain_events", "ix_domain_events_retention", ["occurred_at"], None),
    ("rate_limits", "ix_rate_limits_retention", ["window_start"], None),
    ("idempotency_keys", "ix_idempotency_keys_retention", ["created_at"], None),
    ("sessions", "ix_sessions_retention", ["expires_at"], None),
    ("sessions", "ix_sessions_idle_retention", ["last_seen_at"], None),
    ("sessions", "ix_sessions_revoked_retention", ["revoked_at"], "revoked_at is not null"),
    ("oidc_login_states", "ix_oidc_login_states_retention", ["expires_at"], None),
)


def upgrade() -> None:
    op.create_table(
        "job_dedupe",
        sa.Column("key", sa.String(300), primary_key=True),
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),
    )
    op.execute("grant select, insert, update, delete on job_dedupe to smb_app")
    # The app cannot see other tenants' cached responses. A narrowly scoped
    # definer deletes only genuinely expired rows, including suspended tenants.
    op.execute("""
        create function public.cleanup_expired_idempotency() returns bigint
        language sql security definer set search_path = pg_catalog, public, pg_temp as $$
            with removed as (
                delete from public.idempotency_keys where ctid in (
                    select ctid from public.idempotency_keys
                    where created_at < current_timestamp - interval '24 hours'
                    order by created_at limit 500 for update skip locked
                ) returning 1
            ) select count(*) from removed
        $$
    """)
    op.execute("revoke all on function public.cleanup_expired_idempotency() from public")
    op.execute("grant execute on function public.cleanup_expired_idempotency() to smb_app")
    for table, name, columns, predicate in INDEXES:
        op.create_index(name, table, columns, postgresql_where=sa.text(predicate) if predicate else None)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("select exists(select 1 from job_dedupe)")):
        raise RuntimeError("Cannot drop retained job deduplication receipts; this would permit action replay")
    op.execute("drop function public.cleanup_expired_idempotency()")
    for table, name, _, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_table("job_dedupe")
