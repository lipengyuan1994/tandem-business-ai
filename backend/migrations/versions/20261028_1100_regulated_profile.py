"""Regulated retention holds and indexed read auditing (P8-23).

Revision ID: 20261028_1100_regulated_profile
Revises: 20261028_1000_purchasing_core
"""

from alembic import op

revision = "20261028_1100_regulated_profile"
down_revision = "20261028_1000_purchasing_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create function restrict_mark_erased_suppression(p_business uuid, p_value text) returns bigint
        language plpgsql security definer set search_path = pg_catalog, public, pg_temp as $$
        declare changed bigint;
        begin
          if p_business is distinct from nullif(current_setting('app.business_id', true), '')::uuid then
            raise exception 'restriction is limited to the business of the current session';
          end if;
          update suppressions set reason = 'erased'
           where business_id = p_business and channel = 'email' and value_normalized = p_value and reason <> 'erased';
          get diagnostics changed = row_count;
          return changed;
        end $$
    """)
    op.execute("revoke all on function restrict_mark_erased_suppression(uuid, text) from public")
    op.execute("grant execute on function restrict_mark_erased_suppression(uuid, text) to smb_app")
    op.execute("alter table customers drop constraint ck_customers_status")
    op.execute(
        "alter table customers add constraint ck_customers_status check (status in ('active','archived','merged','erased','restricted'))"
    )
    op.execute("alter table erasure_requests drop constraint ck_erasure_requests_status")
    op.execute(
        "alter table erasure_requests add constraint ck_erasure_requests_status check (status in ('completed','blocked','restricted'))"
    )
    op.execute("drop index ux_erasure_requests_completed")
    op.execute(
        "create unique index ux_erasure_requests_completed on erasure_requests (business_id, customer_id) where status in ('completed','restricted')"
    )
    op.execute(
        "create index ix_audit_events_views on audit_events (business_id, actor_id, action, target_type, target_id, occurred_at desc) where action in ('crm.customer.viewed','conversations.conversation.viewed','scheduling.appointment.viewed')"
    )


def downgrade() -> None:
    # Checks deliberately refuse downgrade while retained restrictions exist; never silently release a hold.
    op.execute("alter table customers drop constraint ck_customers_status")
    op.execute(
        "alter table customers add constraint ck_customers_status check (status in ('active','archived','merged','erased'))"
    )
    op.execute("alter table erasure_requests drop constraint ck_erasure_requests_status")
    op.execute(
        "alter table erasure_requests add constraint ck_erasure_requests_status check (status in ('completed','blocked'))"
    )
    op.execute("drop index ux_erasure_requests_completed")
    op.execute(
        "create unique index ux_erasure_requests_completed on erasure_requests (business_id, customer_id) where status = 'completed'"
    )
    op.execute("drop index ix_audit_events_views")
    op.execute("drop function if exists restrict_mark_erased_suppression(uuid, text)")
