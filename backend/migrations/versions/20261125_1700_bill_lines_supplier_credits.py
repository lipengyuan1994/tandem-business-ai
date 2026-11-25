"""purchasing: supplier bill lines and supplier credits

A bill can list its lines (its total is then their sum). A supplier credit against a bill takes off what is still
owed; any part beyond that is recorded as money the supplier paid back.

Revision ID: 20261125_1700_bill_credits
Revises: 20261125_1600_credits_refunds
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261125_1700_bill_credits"
down_revision = "20261125_1600_credits_refunds"
branch_labels = None
depends_on = None
U = UUID(as_uuid=True)

ENTITY_TYPES_BEFORE = (
    "'customer','supplier','invoice','credit_note','credit_application','invoice_payment','bill','bill_payment','item',"
    "'customer_refund'"
)
ENTITY_TYPES_AFTER = ENTITY_TYPES_BEFORE + ",'supplier_credit'"
METHODS = "'cash','card','bank_transfer','check','other'"
SETTLEMENT = (
    "amount_credited_minor >= 0 and amount_refunded_minor >= 0 "
    "and amount_refunded_minor <= amount_paid_minor and amount_refunded_minor <= amount_credited_minor "
    "and amount_paid_minor - amount_refunded_minor + amount_credited_minor <= total_minor"
)


def _entity_types(values: str) -> None:
    op.drop_constraint(op.f("ck_accounting_sync_records_entity_type"), "accounting_sync_records", type_="check")
    op.create_check_constraint(
        op.f("ck_accounting_sync_records_entity_type"), "accounting_sync_records", f"entity_type in ({values})"
    )


def upgrade() -> None:
    op.add_column(
        "supplier_bills", sa.Column("amount_credited_minor", sa.BigInteger, nullable=False, server_default="0")
    )
    op.add_column(
        "supplier_bills", sa.Column("amount_refunded_minor", sa.BigInteger, nullable=False, server_default="0")
    )
    op.create_check_constraint(op.f("ck_supplier_bills_settlement"), "supplier_bills", SETTLEMENT)

    op.create_table(
        "supplier_bill_lines",
        *scoped_columns(),
        sa.Column("bill_id", U, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_cost_minor", sa.BigInteger, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("catalog_item_id", U),
        sa.Column("purchase_order_line_id", U),
        sa.UniqueConstraint("business_id", "id"),
        sa.UniqueConstraint("business_id", "bill_id", "position"),
        composite_fk("bill_id", "supplier_bills"),
        composite_fk("catalog_item_id", "catalog_items"),
        composite_fk("purchase_order_line_id", "purchase_order_lines"),
        sa.CheckConstraint("quantity > 0 and unit_cost_minor >= 0 and amount_minor >= 0", name="values"),
    )
    scoped_table("supplier_bill_lines")

    op.create_table(
        "supplier_credits",
        *scoped_columns(),
        sa.Column("bill_id", U, nullable=False),
        sa.Column("credit_date", sa.Date, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("supplier_reference", sa.String(100)),
        sa.Column("reason", sa.String(500)),
        sa.Column("refund_minor", sa.BigInteger, nullable=False),
        sa.Column("refund_received_on", sa.Date),
        sa.Column("refund_method", sa.String(16)),
        sa.Column("refund_reference", sa.String(200)),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("business_id", "id"),
        composite_fk("bill_id", "supplier_bills"),
        sa.CheckConstraint("amount_minor > 0 and refund_minor >= 0 and refund_minor <= amount_minor", name="amounts"),
        sa.CheckConstraint(
            "(refund_minor = 0 and refund_received_on is null and refund_method is null and refund_reference is null)"
            " or (refund_minor > 0 and refund_received_on is not null and refund_method is not null)",
            name="refund",
        ),
        sa.CheckConstraint(f"refund_method is null or refund_method in ({METHODS})", name="refund_method"),
    )
    op.create_index("ix_supplier_credits_bill", "supplier_credits", ["business_id", "bill_id"])
    scoped_table("supplier_credits")
    _entity_types(ENTITY_TYPES_AFTER)


def downgrade() -> None:
    # Supplier credits and itemised bills cannot be expressed in the old schema: downgrade only an unused feature.
    conn = op.get_bind()
    used = conn.execute(
        sa.text("select exists(select 1 from supplier_credits) or exists(select 1 from supplier_bill_lines)")
    ).scalar()
    if used:
        raise RuntimeError("supplier credits or bill lines exist; downgrade would lose them")
    op.execute("delete from accounting_sync_records where entity_type = 'supplier_credit'")
    _entity_types(ENTITY_TYPES_BEFORE)
    disable_business_rls("supplier_credits")
    op.drop_table("supplier_credits")
    disable_business_rls("supplier_bill_lines")
    op.drop_table("supplier_bill_lines")
    op.drop_constraint(op.f("ck_supplier_bills_settlement"), "supplier_bills", type_="check")
    op.drop_column("supplier_bills", "amount_refunded_minor")
    op.drop_column("supplier_bills", "amount_credited_minor")
