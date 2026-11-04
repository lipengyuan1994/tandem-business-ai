"""scheduling: resources, resource services, availability rules and exceptions, appointments (no-overlap constraint)

Revision ID: 20261025_1640_scheduling_core
Revises: 20261025_1630_conversations_core
Created (UTC): 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261025_1640_scheduling_core"
down_revision = "20261025_1630_conversations_core"
branch_labels = None
depends_on = None

U, TS = UUID(as_uuid=True), sa.DateTime(timezone=True)
TABLES = ("resources", "resource_services", "availability_rules", "availability_exceptions", "appointments")


def upgrade() -> None:
    op.create_table("resources", *scoped_columns(), sa.Column("location_id", U, nullable=False), sa.Column("kind", sa.String(12), nullable=False),
                    sa.Column("name", sa.String(120), nullable=False), sa.Column("user_id", U, sa.ForeignKey("users.id")),
                    sa.Column("capacity", sa.Integer, nullable=False, server_default="1"), sa.Column("color", sa.String(16)),
                    sa.Column("status", sa.String(10), nullable=False, server_default="active"),
                    sa.UniqueConstraint("business_id", "id"), composite_fk("location_id", "locations"),
                    sa.CheckConstraint("kind in ('staff','room','equipment')", name="kind"),
                    sa.CheckConstraint("status in ('active','archived')", name="status"), sa.CheckConstraint("capacity = 1", name="capacity"))
    op.create_table("resource_services", *scoped_columns(), sa.Column("resource_id", U, nullable=False), sa.Column("service_id", U, nullable=False),
                    sa.UniqueConstraint("business_id", "id"), composite_fk("resource_id", "resources"), composite_fk("service_id", "catalog_items"))
    op.create_index("uq_resource_services", "resource_services", ["resource_id", "service_id"], unique=True)
    op.create_table("availability_rules", *scoped_columns(), sa.Column("resource_id", U, nullable=False),
                    sa.Column("weekday", sa.SmallInteger, nullable=False), sa.Column("start_time", sa.Time, nullable=False),
                    sa.Column("end_time", sa.Time, nullable=False), sa.Column("valid_from", sa.Date), sa.Column("valid_to", sa.Date),
                    sa.UniqueConstraint("business_id", "id"), composite_fk("resource_id", "resources"),
                    sa.CheckConstraint("weekday between 0 and 6", name="weekday"), sa.CheckConstraint("end_time > start_time", name="times"))
    op.create_table("availability_exceptions", *scoped_columns(), sa.Column("resource_id", U), sa.Column("location_id", U, nullable=False),
                    sa.Column("starts_at", TS, nullable=False), sa.Column("ends_at", TS, nullable=False),
                    sa.Column("kind", sa.String(12), nullable=False), sa.Column("reason", sa.String(200)),
                    sa.UniqueConstraint("business_id", "id"), composite_fk("resource_id", "resources"), composite_fk("location_id", "locations"),
                    sa.CheckConstraint("kind in ('closed','extra_open')", name="kind"), sa.CheckConstraint("ends_at > starts_at", name="times"))
    op.create_table("appointments", *scoped_columns(), sa.Column("customer_id", U, nullable=False), sa.Column("service_id", U, nullable=False),
                    sa.Column("resource_id", U, nullable=False), sa.Column("location_id", U, nullable=False),
                    sa.Column("starts_at", TS, nullable=False), sa.Column("ends_at", TS, nullable=False), sa.Column("blocked_until", TS, nullable=False),
                    sa.Column("timezone", sa.String(64), nullable=False), sa.Column("status", sa.String(12), nullable=False),
                    sa.Column("hold_expires_at", TS), sa.Column("proposal_id", U), sa.Column("source", sa.String(16), nullable=False),
                    sa.Column("notes", sa.Text), sa.Column("cancelled_reason", sa.String(300)),
                    sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"), composite_fk("service_id", "catalog_items"),
                    composite_fk("resource_id", "resources"), composite_fk("location_id", "locations"),
                    sa.CheckConstraint("status in ('held','confirmed','cancelled','completed','no_show')", name="status"),
                    sa.CheckConstraint("source in ('staff','assistant','public_request')", name="source"),
                    sa.CheckConstraint("ends_at > starts_at and blocked_until >= ends_at", name="times"),
                    sa.CheckConstraint("status <> 'held' or hold_expires_at is not null", name="hold_expiry"))
    op.create_index("ix_appointments_resource_time", "appointments", ["business_id", "resource_id", "starts_at"])
    # The final guard against double booking (needs btree_gist): no two held/confirmed rows overlap on one resource.
    op.execute("""alter table appointments add constraint appointments_no_overlap exclude using gist
                  (business_id with =, resource_id with =, tstzrange(starts_at, blocked_until, '[)') with &&)
                  where (status in ('held', 'confirmed'))""")
    for table in TABLES:
        scoped_table(table)
    op.execute("""create function businesses_with_expired_holds(p_now timestamptz) returns setof uuid
                  language sql stable security definer set search_path = public as $$
                    select distinct business_id from appointments where status = 'held' and hold_expires_at <= p_now
                  $$""")
    op.execute("revoke all on function businesses_with_expired_holds(timestamptz) from public")
    op.execute("grant execute on function businesses_with_expired_holds(timestamptz) to smb_app")


def downgrade() -> None:
    op.execute("drop function if exists businesses_with_expired_holds(timestamptz)")
    for table in reversed(TABLES):
        disable_business_rls(table)
        op.drop_table(table)
