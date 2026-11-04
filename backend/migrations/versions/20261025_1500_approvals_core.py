"""approvals: proposals, revisions (append-only), decisions (append-only), execution attempts

Revision ID: 20261025_1500_approvals_core
Revises: 20261025_1430_identity_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from migrations.helpers import append_only, create_updated_at_trigger, disable_business_rls, enable_business_rls

revision = "20261025_1500_approvals_core"
down_revision = "20261025_1430_identity_core"
branch_labels = None
depends_on = None

TABLES = ("action_proposals", "proposal_revisions", "approval_decisions", "execution_attempts")
STATUSES = "('pending_review','approved','rejected','expired','cancelled','executing','succeeded','failed')"


def _base() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _fk_proposal() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["business_id", "proposal_id"], ["action_proposals.business_id", "action_proposals.id"])


def upgrade() -> None:
    op.create_table(
        "action_proposals",
        *_base(),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_revision_id", UUID(as_uuid=True)),
        sa.Column("origin_kind", sa.String(16), nullable=False),
        sa.Column("origin_user_id", UUID(as_uuid=True)),
        sa.Column("ai_run_id", UUID(as_uuid=True)),
        sa.Column("conversation_id", UUID(as_uuid=True)),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_code", sa.String(120)),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint(f"status in {STATUSES}", name="status"),
        sa.CheckConstraint("origin_kind in ('staff','assistant','rule')", name="origin_kind"),
    )
    op.create_index("ix_action_proposals_queue", "action_proposals", ["business_id", "status", "created_at"])
    op.create_index("ix_action_proposals_due", "action_proposals", ["expires_at"], postgresql_where=sa.text("status = 'pending_review'"))
    op.create_table(
        "proposal_revisions",
        *_base(),
        sa.Column("proposal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision_no", sa.Integer, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("preview", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by_kind", sa.String(16), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True)),
        sa.UniqueConstraint("business_id", "id"),
        _fk_proposal(),
    )
    op.create_index("uq_proposal_revisions_no", "proposal_revisions", ["proposal_id", "revision_no"], unique=True)
    op.create_table(
        "approval_decisions",
        *_base(),
        sa.Column("proposal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=False),
        sa.Column("roles_at_decision", ARRAY(sa.String(40)), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("business_id", "id"),
        _fk_proposal(),
        sa.ForeignKeyConstraint(["business_id", "revision_id"], ["proposal_revisions.business_id", "proposal_revisions.id"]),
        sa.CheckConstraint("decision in ('approve','reject')", name="decision"),
    )
    op.create_table(
        "execution_attempts",
        *_base(),
        sa.Column("proposal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("acting_user_id", UUID(as_uuid=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(120)),
        sa.Column("error_detail", sa.Text),
        sa.UniqueConstraint("business_id", "id"),
        _fk_proposal(),
        sa.CheckConstraint("status in ('running','succeeded','failed')", name="status"),
    )
    for table in TABLES:
        enable_business_rls(table)
        create_updated_at_trigger(table)
    append_only("proposal_revisions")
    append_only("approval_decisions")
    # Cron scans across businesses without a tenant: only ids of businesses with due proposals are revealed.
    op.execute(
        """
        create function businesses_with_due_proposals(p_now timestamptz) returns setof uuid
        language sql stable security definer set search_path = public as $$
          select distinct business_id from action_proposals where status = 'pending_review' and expires_at < p_now
        $$
        """
    )
    op.execute("revoke all on function businesses_with_due_proposals(timestamptz) from public")
    op.execute("grant execute on function businesses_with_due_proposals(timestamptz) to smb_app")


def downgrade() -> None:
    op.execute("drop function if exists businesses_with_due_proposals(timestamptz)")
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
