"""P10-A1: explicit chat decisions, scoped money reservations and stoppable outbox."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import scoped_columns, scoped_table

revision = "20261105_0900_chat_delivery"
down_revision = "20261105_0800_autonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approval_decisions", sa.Column("decided_via", sa.String(16), nullable=False,
                                                 server_default="approvals"))
    op.create_check_constraint("via", "approval_decisions", "decided_via in ('approvals','chat') and "
        "(decided_via != 'chat' or (decided_by_kind='user' and decision='approve'))")
    for name in ("chat_confirm_counters", "chat_confirm_reservations"):
        extra = ([sa.Column("subject", sa.String(100), nullable=False)] if name.endswith("reservations") else [])
        unique = ["business_id", "day"] + (["subject"] if extra else [])
        check = "total_minor >= 0" + (" and total_minor <= 100000" if not extra else "")
        op.create_table(name, *scoped_columns(), sa.Column("day", sa.Date, nullable=False), *extra,
            sa.Column("total_minor", sa.BigInteger, nullable=False, server_default="0"),
            sa.UniqueConstraint("business_id", "id"), sa.UniqueConstraint(*unique),
            sa.CheckConstraint(check, name="total"))
        op.execute(f"grant select, insert, update, delete on {name} to smb_app")
        scoped_table(name)
    op.add_column("outbox_messages", sa.Column("not_before", sa.DateTime(timezone=True)))
    op.drop_constraint("status", "outbox_messages", type_="check")
    op.create_check_constraint("status", "outbox_messages",
                               "status in ('pending','sending','delivered','failed','unknown','cancelled')")
    op.add_column("suppliers", sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("select exists(select 1 from outbox_messages where status='cancelled')")):
        raise RuntimeError("Cannot discard stopped delivery history")
    if bind.scalar(sa.text("select exists(select 1 from approval_decisions where decided_via='chat')")):
        raise RuntimeError("Cannot discard explicit chat approval provenance")
    if bind.scalar(sa.text("select exists(select 1 from outbox_messages "
                           "where status='pending' and not_before is not null)")):
        raise RuntimeError("Cannot discard scheduled delivery delay")
    if bind.scalar(sa.text("select exists(select 1 from suppliers where active=false)")):
        raise RuntimeError("Cannot reactivate inactive suppliers during downgrade")
    op.drop_column("suppliers", "active")
    op.drop_constraint("status", "outbox_messages", type_="check")
    op.create_check_constraint("status", "outbox_messages",
                               "status in ('pending','sending','delivered','failed','unknown')")
    op.drop_column("outbox_messages", "not_before")
    for name in ("chat_confirm_reservations", "chat_confirm_counters"):
        op.drop_table(name)
    op.drop_constraint("via", "approval_decisions", type_="check")
    op.drop_column("approval_decisions", "decided_via")
