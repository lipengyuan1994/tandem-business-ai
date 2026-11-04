"""platform tables: jobs, domain events, deliveries, audit, idempotency, rate limits

Revision ID: 20261025_0110_platform_tables
Revises: 20261025_0100_platform_base
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import append_only, create_updated_at_trigger, disable_business_rls, enable_business_rls

revision = "20261025_0110_platform_tables"
down_revision = "20261025_0100_platform_base"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _ts(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=None if nullable else NOW)


def upgrade() -> None:
    # Queue tables are exempt from row-level security: the worker claims across businesses (05-data-model.md).
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id")),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("priority", sa.SmallInteger, nullable=False, server_default=sa.text("100")),
        _ts("run_at"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("8")),
        sa.Column("locked_by", sa.String(100)),
        _ts("locked_until", nullable=True),
        sa.Column("dedupe_key", sa.String(300), unique=True),
        sa.Column("last_error", sa.Text),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("status in ('queued','running','succeeded','failed','dead')", name="status"),
    )
    op.create_index("ix_jobs_ready", "jobs", ["priority", "run_at"], postgresql_where=sa.text("status = 'queued'"))
    create_updated_at_trigger("jobs")

    op.create_table(
        "domain_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("schema_version", sa.SmallInteger, nullable=False, server_default=sa.text("1")),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True)),
        sa.Column("request_id", sa.String(64)),
        _ts("occurred_at"),
    )
    op.create_table(
        "event_deliveries",
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("domain_events.id"), primary_key=True),
        sa.Column("subscriber", sa.String(150), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        _ts("run_at"),
        _ts("locked_until", nullable=True),
        sa.Column("last_error", sa.Text),
        _ts("done_at", nullable=True),
        sa.CheckConstraint("status in ('pending','running','done','dead')", name="status"),
    )
    op.create_index("ix_event_deliveries_ready", "event_deliveries", ["run_at"], postgresql_where=sa.text("status = 'pending'"))

    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False),
        _ts("occurred_at"),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True)),
        sa.Column("on_behalf_of_user_id", UUID(as_uuid=True)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(60), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("proposal_id", UUID(as_uuid=True)),
        sa.Column("ai_run_id", UUID(as_uuid=True)),
        sa.Column("request_id", sa.String(64)),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("changes", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("actor_kind in ('user','assistant','system','visitor')", name="actor_kind"),
    )
    op.create_index("ix_audit_events_business_time", "audit_events", ["business_id", "occurred_at"])
    op.create_index("ix_audit_events_target", "audit_events", ["business_id", "target_type", "target_id"])
    enable_business_rls("audit_events")
    append_only("audit_events")

    op.create_table(
        "idempotency_keys",
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), primary_key=True),
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer, nullable=False),
        sa.Column("response_body", JSONB, nullable=False),
        _ts("created_at"),
    )
    enable_business_rls("idempotency_keys")

    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    disable_business_rls("idempotency_keys")
    op.drop_table("idempotency_keys")
    disable_business_rls("audit_events")
    op.drop_table("audit_events")
    op.drop_table("event_deliveries")
    op.drop_table("domain_events")
    op.drop_table("jobs")
