"""P10-A0: scoped autonomy rules, append-only evaluations and delegated policy decisions."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from migrations.helpers import composite_fk, scoped_columns, scoped_table

revision = "20261105_0800_autonomy"
down_revision = "20261104_0700_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autonomy_rules", *scoped_columns(),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="ask"),
        sa.Column("shadow", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("daily_cap", sa.Integer, nullable=False, server_default="10"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("granted_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "action_type"),
        sa.CheckConstraint("level in ('off','draft','ask','act_notify','act')", name="level"),
        sa.CheckConstraint("daily_cap >= 0 and version > 0", name="limits"),
    )
    op.create_table(
        "autonomy_evaluations", *scoped_columns(),
        sa.Column("proposal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_version", sa.Integer, nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reasons", ARRAY(sa.String(40)), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "proposal_id"),
        composite_fk("proposal_id", "action_proposals"), composite_fk("rule_id", "autonomy_rules"),
        sa.CheckConstraint("outcome in ('acted','would_act','fell_back','blocked')", name="outcome"),
        sa.CheckConstraint("rule_version > 0", name="rule_version"),
    )
    op.create_index("ix_autonomy_evaluations_activity", "autonomy_evaluations", ["business_id", "created_at", "id"])
    op.create_table(
        "autonomy_counters", *scoped_columns(),
        sa.Column("rule_id", UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint("business_id", "rule_id", "day"),
        composite_fk("rule_id", "autonomy_rules"), sa.CheckConstraint("count >= 0", name="count"),
    )
    for table in ("autonomy_rules", "autonomy_evaluations", "autonomy_counters"):
        op.execute(f"grant select, insert, update, delete on {table} to smb_app")
        scoped_table(table, append=table == "autonomy_evaluations")
    op.add_column("approval_decisions", sa.Column("decided_by_kind", sa.String(10), nullable=False, server_default="user"))
    op.add_column("approval_decisions", sa.Column("rule_id", UUID(as_uuid=True)))
    op.add_column("approval_decisions", sa.Column("rule_version", sa.Integer))
    op.add_column("approval_decisions", sa.Column("granted_by", UUID(as_uuid=True), sa.ForeignKey("users.id")))
    op.alter_column("approval_decisions", "decided_by", nullable=True)
    op.create_foreign_key("fk_approval_decisions_business_id_rule_id_autonomy_rules", "approval_decisions", "autonomy_rules",
                          ["business_id", "rule_id"], ["business_id", "id"])
    op.create_check_constraint("delegation", "approval_decisions",
        "(decided_by_kind='user' and decided_by is not null and rule_id is null "
        "and rule_version is null and granted_by is null) or "
        "(decided_by_kind='policy' and decided_by is null and rule_id is not null "
        "and rule_version is not null and rule_version > 0 and granted_by is not null and decision='approve')")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("select exists(select 1 from approval_decisions where decided_by_kind='policy')")):
        raise RuntimeError("Cannot discard delegated decisions or fabricate human approvers")
    op.drop_constraint("delegation", "approval_decisions", type_="check")
    op.drop_constraint("fk_approval_decisions_business_id_rule_id_autonomy_rules", "approval_decisions", type_="foreignkey")
    op.drop_constraint("fk_approval_decisions_granted_by_users", "approval_decisions", type_="foreignkey")
    op.alter_column("approval_decisions", "decided_by", nullable=False)
    for col in ("granted_by", "rule_version", "rule_id", "decided_by_kind"):
        op.drop_column("approval_decisions", col)
    for table in ("autonomy_counters", "autonomy_evaluations", "autonomy_rules"):
        op.drop_table(table)
