"""Keep public request contact consent evidence on conversations.

Revision ID: 20261028_1400_contact_consent
Revises: 20261028_1300_business_hardening
"""

from alembic import op

revision = "20261028_1400_contact_consent"
down_revision = "20261028_1300_business_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table conversations add column contact_consent_at timestamptz")
    op.execute("alter table conversations add column contact_consent_text text")
    op.execute("""alter table conversations add constraint ck_conversations_contact_consent_evidence
        check ((contact_consent_at is null) = (contact_consent_text is null))""")


def downgrade() -> None:
    op.execute("alter table conversations drop constraint ck_conversations_contact_consent_evidence")
    op.execute("alter table conversations drop column contact_consent_text")
    op.execute("alter table conversations drop column contact_consent_at")
