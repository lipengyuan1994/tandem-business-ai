"""P10-C2b customer capability access, lifecycle revocation and change receipts."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID
from migrations.helpers import composite_fk, scoped_columns, scoped_table

revision = "20261106_1400_self_service"
down_revision = "20261106_1200_customer_care"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("self_service_tokens", "self_service_sessions"):
        extra = [sa.Column("used_at", sa.DateTime(timezone=True))] if name.endswith("tokens") else []
        op.create_table(name, *scoped_columns(),
            sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)), *extra,
            composite_fk("customer_id", "customers"), sa.UniqueConstraint("business_id", "id"))
        op.create_index("uq_self_service_token" if name.endswith("tokens") else "uq_self_service_session", name, ["token_hash"], unique=True)
        op.create_index(f"ix_{name}_customer", name, ["business_id", "customer_id"])
    op.create_table("self_service_changes", *scoped_columns(),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=False),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("fee_acknowledged", sa.Boolean, nullable=False),
        sa.Column("fee_text", sa.String(1000)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("appointment_version", sa.Integer, nullable=False),
        composite_fk("customer_id", "customers"), composite_fk("appointment_id", "appointments"),
        sa.UniqueConstraint("business_id", "id"))
    for name in ("self_service_tokens", "self_service_sessions", "self_service_changes"):
        op.execute(f"grant select, insert, update, delete on {name} to smb_app")
        scoped_table(name)
    # Customer mutations already hold the customer lock before touching downstream data.
    # A trigger covers every lifecycle path, including archive/unarchive, without a reverse module dependency.
    op.execute("""create function revoke_customer_self_service() returns trigger language plpgsql as $$
        begin
          if new.status <> 'active' then
            update self_service_tokens set revoked_at = now() where business_id=new.business_id and customer_id=new.id and revoked_at is null;
            update self_service_sessions set revoked_at = now() where business_id=new.business_id and customer_id=new.id and revoked_at is null;
          end if;
          if new.status <> 'active' or new.preferred_contact_channel <> 'email' then
            update outbox_messages set status='cancelled', restricted_at=now()
              where business_id=new.business_id and related_type='customer' and related_id=new.id
                and template_key in ('self_service_link','self_service_confirmation')
                and status in ('pending','unknown');
          end if;
          return new;
        end $$""")
    op.execute("create trigger customers_revoke_self_service after update of status, preferred_contact_channel on customers for each row execute function revoke_customer_self_service()")


def downgrade():
    op.execute("drop trigger customers_revoke_self_service on customers")
    op.execute("drop function revoke_customer_self_service()")
    for name in ("self_service_changes", "self_service_sessions", "self_service_tokens"):
        op.drop_table(name)
