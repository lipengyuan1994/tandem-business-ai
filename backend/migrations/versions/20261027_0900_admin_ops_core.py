"""admin_ops: erasure requests, and the security-definer functions that redact append-only evidence

Revision ID: 20261027_0900_admin_ops_core
Revises: 20261026_1000_marketing_core
Created (UTC): 2026-10-27

The audit log and approval revisions are append-only for the application role (it cannot UPDATE them). Erasing a person
must still be able to remove their name from the text of those records, so two narrow functions do exactly that. Each
refuses to run for any business other than the one set in the session (`app.business_id`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

from migrations.helpers import composite_fk, disable_business_rls, scoped_columns, scoped_table

revision = "20261027_0900_admin_ops_core"
down_revision = "20261026_1000_marketing_core"
branch_labels = None
depends_on = None

U = UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

REDACT_TEXT = """
create function redact_text(t text, needles text[]) returns text language plpgsql immutable as $$
declare n text;
begin
  if t is null then return null; end if;
  foreach n in array needles loop
    t := replace(t, n, '[erased]');
  end loop;
  return t;
end $$
"""

REDACT_JSONB = """
create function redact_jsonb(j jsonb, needles text[]) returns jsonb language plpgsql immutable as $$
declare n text; s text; e text;
begin
  if j is null then return null; end if;
  s := j::text;
  foreach n in array needles loop
    e := substr(to_json(n)::text, 2, length(to_json(n)::text) - 2);
    s := replace(s, e, '[erased]');
  end loop;
  return s::jsonb;
end $$
"""

GUARD = """
  if p_business is distinct from nullif(current_setting('app.business_id', true), '')::uuid then
    raise exception 'erasure is limited to the business of the current session';
  end if;
"""

ERASE_AUDIT = f"""
create function erase_redact_audit(p_business uuid, p_needles text[]) returns bigint
language plpgsql security definer set search_path = public as $$
declare c bigint;
begin
{GUARD}
  update audit_events set summary = redact_text(summary, p_needles), changes = redact_jsonb(changes, p_needles)
   where business_id = p_business
     and (summary is distinct from redact_text(summary, p_needles) or changes is distinct from redact_jsonb(changes, p_needles));
  get diagnostics c = row_count;
  return c;
end $$
"""

ERASE_PROPOSALS = f"""
create function erase_redact_proposals(p_business uuid, p_needles text[]) returns bigint
language plpgsql security definer set search_path = public as $$
declare c1 bigint; c2 bigint; c3 bigint;
begin
{GUARD}
  update action_proposals set title = redact_text(title, p_needles)
   where business_id = p_business and title is distinct from redact_text(title, p_needles);
  get diagnostics c1 = row_count;
  update proposal_revisions
     set payload = redact_jsonb(payload, p_needles), summary = redact_text(summary, p_needles),
         evidence = redact_jsonb(evidence, p_needles), preview = redact_jsonb(preview, p_needles)
   where business_id = p_business
     and (payload is distinct from redact_jsonb(payload, p_needles) or summary is distinct from redact_text(summary, p_needles)
          or evidence is distinct from redact_jsonb(evidence, p_needles) or preview is distinct from redact_jsonb(preview, p_needles));
  get diagnostics c2 = row_count;
  update approval_decisions set comment = redact_text(comment, p_needles)
   where business_id = p_business and comment is distinct from redact_text(comment, p_needles);
  get diagnostics c3 = row_count;
  return c1 + c2 + c3;
end $$
"""


ERASE_CONSENT = f"""
create function erase_redact_consent(p_business uuid, p_customer uuid) returns bigint
language plpgsql security definer set search_path = public as $$
declare c bigint;
begin
{GUARD}
  update consent_records set evidence = '[erased]', recorded_by = null
   where business_id = p_business and customer_id = p_customer and (evidence <> '[erased]' or recorded_by is not null);
  get diagnostics c = row_count;
  return c;
end $$
"""


def upgrade() -> None:
    op.create_table(
        "erasure_requests", *scoped_columns(),
        sa.Column("customer_id", U, nullable=False),
        sa.Column("requested_by", U),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("report", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("completed_at", TS),
        sa.UniqueConstraint("business_id", "id"), composite_fk("customer_id", "customers"),
        sa.CheckConstraint("status in ('completed','blocked')", name="status"),
    )
    scoped_table("erasure_requests")
    for statement in (REDACT_TEXT, REDACT_JSONB, ERASE_AUDIT, ERASE_PROPOSALS, ERASE_CONSENT):
        op.execute(statement)


def downgrade() -> None:
    for fn in ("erase_redact_consent(uuid, uuid)", "erase_redact_proposals(uuid, text[])", "erase_redact_audit(uuid, text[])", "redact_jsonb(jsonb, text[])", "redact_text(text, text[])"):
        op.execute(f"drop function if exists {fn}")
    disable_business_rls("erasure_requests")
    op.drop_table("erasure_requests")
