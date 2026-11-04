"""Helpers every migration uses (04-repository-and-conventions.md#migrations)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

TENANT = "nullif(current_setting('app.business_id', true), '')::uuid"


def enable_business_rls(table: str, column: str = "business_id") -> None:
    """Row-level security: rows visible and writable only for the transaction's business. Fails closed."""
    op.execute(f"alter table {table} enable row level security")
    op.execute(f"alter table {table} force row level security")
    op.execute(
        f"create policy business_isolation on {table} using ({column} = {TENANT}) with check ({column} = {TENANT})"
    )


def disable_business_rls(table: str) -> None:
    op.execute(f"drop policy if exists business_isolation on {table}")
    op.execute(f"alter table {table} no force row level security")
    op.execute(f"alter table {table} disable row level security")


def create_updated_at_trigger(table: str) -> None:
    op.execute(
        f"create trigger {table}_updated_at before update on {table} for each row execute function set_updated_at()"
    )


def drop_updated_at_trigger(table: str) -> None:
    op.execute(f"drop trigger if exists {table}_updated_at on {table}")


def append_only(table: str, role: str = "smb_app") -> None:
    """The application role may insert and read, never update or delete."""
    op.execute(f"revoke update, delete, truncate on {table} from {role}")


def scoped_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    """Standard columns of every business-scoped table (pair with sa.UniqueConstraint("business_id", "id"))."""
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", UUID(as_uuid=True), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def composite_fk(local: str, table: str) -> sa.ForeignKeyConstraint:
    """(business_id, local) -> table(business_id, id): a row can never point into another business."""
    return sa.ForeignKeyConstraint(["business_id", local], [f"{table}.business_id", f"{table}.id"])


def scoped_table(table: str, append: bool = False) -> None:
    """RLS + updated_at trigger (+ append-only grants) for a new business-scoped table."""
    enable_business_rls(table)
    create_updated_at_trigger(table)
    if append:
        append_only(table)
