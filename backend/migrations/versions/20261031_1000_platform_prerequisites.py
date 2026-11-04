"""Active-business cron discovery with a hardened SECURITY DEFINER function.

Revision ID: 20261031_1000_platform_prerequisites
Revises: 20261028_1400_contact_consent
"""

from alembic import op

revision = "20261031_1000_platform_prerequisites"
down_revision = "20261028_1400_contact_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""create function public.active_business_ids() returns setof uuid
                  language sql stable security definer
                  set search_path = pg_catalog, public, pg_temp as $$
                    select id from public.businesses where status = 'active'
                  $$""")
    op.execute("revoke all on function public.active_business_ids() from public")
    op.execute("grant execute on function public.active_business_ids() to smb_app")


def downgrade() -> None:
    op.execute("drop function if exists public.active_business_ids()")
