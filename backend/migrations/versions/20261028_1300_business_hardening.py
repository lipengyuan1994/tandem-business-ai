"""Mark erased threads durably and preserve erasure suppressions across a full rollback.

Revision ID: 20261028_1300_business_hardening
Revises: 20261028_1200_regulated_hardening
"""

from alembic import op

revision = "20261028_1300_business_hardening"
down_revision = "20261028_1200_regulated_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table conversations add column erased_at timestamptz")
    # Earlier erasures blanked visitor-only threads without linking the customer. Find them via the
    # erased messages as well as linked customers, so their outstanding credentials are revoked too.
    op.execute("""update conversations c set erased_at=now()
        where exists (select 1 from customers p where p.business_id=c.business_id
                       and p.id=c.customer_id and p.status='erased')
           or (c.visitor_name is null and c.visitor_email is null and c.visitor_phone is null
               and c.subject is null
               and exists (select 1 from messages m where m.business_id=c.business_id
                            and m.conversation_id=c.id and m.body='[erased]'))""")
    op.execute("""update visitor_tokens v set revoked_at=coalesce(v.revoked_at,now())
        where exists (select 1 from conversations c where c.business_id=v.business_id
                       and c.id=v.conversation_id and c.erased_at is not null)""")


def downgrade() -> None:
    # This runs before the historic 20261027_1500 downgrade restores its narrower constraint.
    # Keep the address suppressed; 'manual' already has the same delivery exclusion semantics.
    op.execute("update suppressions set reason='manual' where reason='erased'")
    op.execute("alter table conversations drop column erased_at")
