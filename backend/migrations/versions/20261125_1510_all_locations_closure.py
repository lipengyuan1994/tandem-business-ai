"""scheduling: a closure with no location closes every location

Revision ID: 20261125_1510_all_loc_closure
Revises: 20261125_1500_cp_archived
"""

from alembic import op

revision = "20261125_1510_all_loc_closure"
down_revision = "20261125_1500_cp_archived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("availability_exceptions", "location_id", nullable=True)
    op.create_check_constraint(
        "all_locations_closure",
        "availability_exceptions",
        "location_id is not null or (resource_id is null and kind = 'closed')",
    )


def downgrade() -> None:
    # Closures for every location become one closure per location before the column is required again.
    op.execute(
        """
        insert into availability_exceptions
               (id, business_id, version, created_at, updated_at, resource_id, location_id, starts_at, ends_at,
                kind, reason)
        select gen_random_uuid(), e.business_id, 1, e.created_at, e.updated_at, null, l.id, e.starts_at, e.ends_at,
               e.kind, e.reason
          from availability_exceptions e join locations l on l.business_id = e.business_id
         where e.location_id is null
        """
    )
    op.execute("delete from availability_exceptions where location_id is null")
    op.drop_constraint(op.f("ck_availability_exceptions_all_locations_closure"), "availability_exceptions", type_="check")
    op.alter_column("availability_exceptions", "location_id", nullable=False)
