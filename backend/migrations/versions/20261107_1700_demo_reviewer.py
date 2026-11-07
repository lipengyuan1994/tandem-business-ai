"""P10-J: allow the limited public demo reviewer role."""

from alembic import op

revision = "20261107_1700_demo_reviewer"
down_revision = "20261107_1600_reply_guardrail"
branch_labels = None
depends_on = None

ROLES = "'owner','admin','operator','inventory_manager','marketing_manager','auditor'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_role_grants_role"), "role_grants", type_="check")
    op.create_check_constraint(op.f("ck_role_grants_role"), "role_grants", f"role in ({ROLES},'demo_reviewer')")


def downgrade() -> None:
    # Retain grants/history rather than silently stripping a user's role.
    op.execute(
        "do $$ begin if exists (select 1 from role_grants where role='demo_reviewer') "
        "then raise exception 'Remove demo reviewer grants before downgrading'; end if; end $$"
    )
    op.drop_constraint(op.f("ck_role_grants_role"), "role_grants", type_="check")
    op.create_check_constraint(op.f("ck_role_grants_role"), "role_grants", f"role in ({ROLES})")
