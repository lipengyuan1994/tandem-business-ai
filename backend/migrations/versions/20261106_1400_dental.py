"""P10-E1 multi-resource occupancy and synthetic demo marker; intentionally no backfill."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB
from migrations.helpers import scoped_columns, scoped_table, composite_fk

revision = "20261106_1400_dental"
down_revision = "20261106_1300_knowledge_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("businesses", sa.Column("synthetic_demo", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("catalog_items", sa.Column("required_resources", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("conversations", sa.Column("emergency", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_table("appointment_resources", *scoped_columns(),
        sa.Column("appointment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        sa.ForeignKeyConstraint(["business_id", "appointment_id"], ["appointments.business_id", "appointments.id"], ondelete="CASCADE"),
        composite_fk("resource_id", "resources"),
        sa.CheckConstraint("blocked_until > starts_at", name="times"))
    op.create_index("uq_appointment_resources_resource", "appointment_resources", ["appointment_id", "resource_id"], unique=True)
    op.execute("""alter table appointment_resources add constraint appointment_resources_no_overlap
        exclude using gist (business_id with =, resource_id with =, tstzrange(starts_at, blocked_until, '[)') with &&)
        where (active)""")
    op.execute("grant select, insert, update, delete on appointment_resources to smb_app")
    scoped_table("appointment_resources")
    op.execute("""create function synthetic_demo_exists() returns boolean language sql stable
        security definer set search_path=pg_catalog,public,pg_temp as $$
        select exists(select 1 from public.businesses where synthetic_demo)
        $$""")
    op.execute("revoke all on function synthetic_demo_exists() from public")
    op.execute("grant execute on function synthetic_demo_exists() to smb_app")
    # Every future parent write mirrors its primary into the same exclusion domain as secondary resources.
    # Old parents need no backfill: the insert trigger checks their original primary exclusion domain too.
    op.execute("""create function validate_appointment_resource() returns trigger language plpgsql as $$
    declare a appointments%rowtype;
    begin
      select * into strict a from appointments where business_id=new.business_id and id=new.appointment_id;
      new.starts_at := a.starts_at;
      new.blocked_until := a.blocked_until;
      new.active := a.status in ('held','confirmed');
      if new.active and exists(select 1 from appointments p where p.business_id=new.business_id
          and p.id<>new.appointment_id and p.resource_id=new.resource_id
          and p.status in ('held','confirmed')
          and tstzrange(p.starts_at,p.blocked_until,'[)') && tstzrange(new.starts_at,new.blocked_until,'[)')) then
        raise exclusion_violation using message='Resource overlaps an existing primary appointment';
      end if;
      return new;
    end $$""")
    op.execute("""create trigger appointment_resources_validate before insert or update on appointment_resources
        for each row execute function validate_appointment_resource()""")
    op.execute("""create function sync_appointment_resources() returns trigger language plpgsql as $$
    begin
      if TG_OP='UPDATE' and old.resource_id<>new.resource_id then
        delete from appointment_resources where business_id=new.business_id and appointment_id=new.id and resource_id=old.resource_id;
      end if;
      update appointment_resources set starts_at=new.starts_at, blocked_until=new.blocked_until,
        active=new.status in ('held','confirmed') where business_id=new.business_id and appointment_id=new.id;
      insert into appointment_resources(id,business_id,appointment_id,resource_id,role,starts_at,blocked_until,active)
        values(gen_random_uuid(),new.business_id,new.id,new.resource_id,'primary',new.starts_at,new.blocked_until,new.status in ('held','confirmed'))
        on conflict (appointment_id,resource_id) do nothing;
      return new;
    end $$""")
    op.execute("""create trigger appointments_sync_resources after insert or update of starts_at,blocked_until,status,resource_id
        on appointments for each row execute function sync_appointment_resources()""")


def downgrade():
    op.execute("drop function if exists synthetic_demo_exists()")
    op.execute("drop trigger appointments_sync_resources on appointments")
    op.execute("drop function sync_appointment_resources()")
    op.drop_table("appointment_resources")
    op.execute("drop function validate_appointment_resource()")
    op.drop_column("conversations", "emergency")
    op.drop_column("catalog_items", "required_resources")
    op.drop_column("businesses", "synthetic_demo")
