"""sales: partial credit notes and refunds to customers

An invoice can now have several credit notes, each for some of its lines. A credit note larger than what is still
owed pays the difference back to the customer as a refund. Invoices already credited in full are backfilled with
the amount credited.

Revision ID: 20261125_1600_credits_refunds
Revises: 20261125_1510_all_loc_closure
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261125_1600_credits_refunds"
down_revision = "20261125_1510_all_loc_closure"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)

ENTITY_TYPES_BEFORE = (
    "'customer','supplier','invoice','credit_note','credit_application','invoice_payment','bill','bill_payment','item'"
)
ENTITY_TYPES_AFTER = ENTITY_TYPES_BEFORE + ",'customer_refund'"

SETTLEMENT = (
    "amount_credited_minor >= 0 and amount_refunded_minor >= 0 "
    "and amount_refunded_minor <= amount_paid_minor and amount_refunded_minor <= amount_credited_minor "
    "and amount_paid_minor - amount_refunded_minor + amount_credited_minor <= total_minor"
)


def upgrade() -> None:
    op.add_column("invoices", sa.Column("amount_credited_minor", sa.BigInteger, nullable=False, server_default="0"))
    op.add_column("invoices", sa.Column("amount_refunded_minor", sa.BigInteger, nullable=False, server_default="0"))
    op.execute(
        """
        update invoices i set amount_credited_minor = cn.total_minor
        from invoices cn
        where cn.business_id = i.business_id and cn.credit_of_invoice_id = i.id and cn.kind = 'credit_note'
        """
    )
    op.create_check_constraint(op.f("ck_invoices_settlement"), "invoices", SETTLEMENT)
    op.drop_constraint("uq_invoices_business_id_credit_of_invoice_id", "invoices", type_="unique")
    op.create_index("ix_invoices_credit_of_invoice", "invoices", ["business_id", "credit_of_invoice_id"])

    op.add_column("invoice_lines", sa.Column("credited_line_id", U))
    op.create_foreign_key(
        op.f("fk_invoice_lines_business_id_credited_line_id_invoice_lines"),
        "invoice_lines",
        "invoice_lines",
        ["business_id", "credited_line_id"],
        ["business_id", "id"],
    )

    op.create_table(
        "invoice_refunds",
        *scoped_columns(),
        sa.Column("invoice_id", U, nullable=False),
        sa.Column("credit_note_id", U, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("paid_on", sa.Date, nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(200)),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "credit_note_id"),
        composite_fk("invoice_id", "invoices"),
        composite_fk("credit_note_id", "invoices"),
        sa.CheckConstraint("amount_minor > 0", name="amount"),
        sa.CheckConstraint("method in ('cash','card','bank_transfer','check','other')", name="method"),
    )
    op.create_index("ix_invoice_refunds_invoice", "invoice_refunds", ["business_id", "invoice_id"])
    scoped_table("invoice_refunds")
    op.drop_constraint(op.f("ck_accounting_sync_records_entity_type"), "accounting_sync_records", type_="check")
    op.create_check_constraint(
        op.f("ck_accounting_sync_records_entity_type"),
        "accounting_sync_records",
        f"entity_type in ({ENTITY_TYPES_AFTER})",
    )


def downgrade() -> None:
    # Refunds and partial credits cannot be expressed in the old schema: downgrade only an unused feature.
    conn = op.get_bind()
    used = conn.execute(
        sa.text(
            "select exists(select 1 from invoice_refunds) or exists("
            "select 1 from invoices where amount_credited_minor not in (0, total_minor))"
        )
    ).scalar()
    if used:
        raise RuntimeError("partial credit notes or refunds exist; downgrade would lose them")
    op.execute("delete from accounting_sync_records where entity_type = 'customer_refund'")
    op.drop_constraint(op.f("ck_accounting_sync_records_entity_type"), "accounting_sync_records", type_="check")
    op.create_check_constraint(
        op.f("ck_accounting_sync_records_entity_type"),
        "accounting_sync_records",
        f"entity_type in ({ENTITY_TYPES_BEFORE})",
    )
    disable_business_rls("invoice_refunds")
    op.drop_table("invoice_refunds")
    op.drop_constraint(
        op.f("fk_invoice_lines_business_id_credited_line_id_invoice_lines"), "invoice_lines", type_="foreignkey"
    )
    op.drop_column("invoice_lines", "credited_line_id")
    op.drop_index("ix_invoices_credit_of_invoice", table_name="invoices")
    op.create_unique_constraint(
        "uq_invoices_business_id_credit_of_invoice_id", "invoices", ["business_id", "credit_of_invoice_id"]
    )
    op.drop_constraint(op.f("ck_invoices_settlement"), "invoices", type_="check")
    op.drop_column("invoices", "amount_refunded_minor")
    op.drop_column("invoices", "amount_credited_minor")
