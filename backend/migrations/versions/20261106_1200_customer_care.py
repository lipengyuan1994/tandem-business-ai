"""P10-B2 Customer Care preferences, recall configuration, cases and delivery receipts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID
from migrations.helpers import scoped_columns, scoped_table

revision = "20261106_1200_customer_care"
down_revision = "20261106_1000_ai_team"
branch_labels = None
depends_on = None


def fk(col, table):
    return sa.ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


CASES = "'intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft','payment_followup','safety','grounding','dictation'"


def upgrade():
    op.drop_constraint("use_case", "ai_runs", type_="check")
    op.create_check_constraint("use_case", "ai_runs", f"use_case in ({CASES},'customer_care')")
    op.add_column("customers", sa.Column("preferred_contact_channel", sa.String(10), nullable=False, server_default="email"))
    op.create_check_constraint("ck_customers_contact_preference", "customers", "preferred_contact_channel in ('email','web_chat','none')")
    op.add_column("catalog_items", sa.Column("recall_interval_months", sa.Integer))
    op.create_check_constraint("ck_catalog_items_recall_interval", "catalog_items", "recall_interval_months is null or (kind = 'service' and recall_interval_months between 1 and 120)")
    op.create_table("customer_care_cases", *scoped_columns(),
        sa.Column("customer_id", UUID(as_uuid=True)), sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(16), nullable=False), sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False), sa.Column("appointment_id", UUID(as_uuid=True)),
        sa.Column("invoice_id", UUID(as_uuid=True)), sa.Column("proposal_id", UUID(as_uuid=True)),
        sa.Column("resolved_by", UUID(as_uuid=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"),
        *[fk(c,t) for c,t in [("customer_id","customers"),("conversation_id","conversations"),("source_message_id","messages"),
                              ("appointment_id","appointments"),("invoice_id","invoices"),("proposal_id","action_proposals")]],
        sa.CheckConstraint("category in ('complaint','warranty','dispute')", name="category"),
        sa.CheckConstraint("priority in ('normal','high','urgent')", name="priority"),
        sa.CheckConstraint("status in ('open','resolved')", name="status"))
    op.create_index("uq_care_case_source", "customer_care_cases", ["business_id", "source_message_id"], unique=True)
    op.create_index("ix_care_case_customer", "customer_care_cases", ["business_id", "customer_id", "status"])
    op.create_table("customer_care_deliveries", *scoped_columns(),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False), sa.Column("domain_key", sa.String(240), nullable=False),
        sa.Column("proposal_id", UUID(as_uuid=True), nullable=False), sa.Column("outbox_id", UUID(as_uuid=True)),
        sa.Column("message_id", UUID(as_uuid=True)), sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_message_id", UUID(as_uuid=True)), sa.Column("appointment_id", UUID(as_uuid=True)),
        sa.Column("service_id", UUID(as_uuid=True)), sa.Column("review_requested", sa.Boolean, nullable=False),
        sa.Column("due_month", sa.String(7)),
        sa.Column("ai_generation", sa.Integer),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("business_id", "id"),
        *[fk(c,t) for c,t in [("customer_id","customers"),("proposal_id","action_proposals"),("message_id","messages"),
                              ("source_message_id","messages"),("appointment_id","appointments"),("service_id","catalog_items")]])
    op.create_index("uq_care_delivery_subject", "customer_care_deliveries", ["business_id", "domain_key"], unique=True)
    op.create_index("ix_care_delivery_outbox", "customer_care_deliveries", ["business_id", "outbox_id"])
    op.create_index("uq_care_delivery_proposal", "customer_care_deliveries", ["business_id", "proposal_id"], unique=True)
    for table in ("customer_care_cases", "customer_care_deliveries"):
        op.execute(f"grant select, insert, update, delete on {table} to smb_app")
        scoped_table(table)


def downgrade():
    op.drop_table("customer_care_deliveries")
    op.drop_table("customer_care_cases")
    op.drop_constraint("ck_catalog_items_recall_interval", "catalog_items", type_="check")
    op.drop_column("catalog_items", "recall_interval_months")
    op.drop_constraint("ck_customers_contact_preference", "customers", type_="check")
    op.drop_column("customers", "preferred_contact_channel")
    op.execute("delete from ai_effects where run_id in (select id from ai_runs where use_case='customer_care')")
    op.execute("delete from ai_run_steps where run_id in (select id from ai_runs where use_case='customer_care')")
    op.execute("update action_proposals set ai_run_id=null where ai_run_id in (select id from ai_runs where use_case='customer_care')")
    op.execute("delete from ai_runs where use_case='customer_care'")
    op.drop_constraint("use_case", "ai_runs", type_="check")
    op.create_check_constraint("use_case", "ai_runs", f"use_case in ({CASES})")
