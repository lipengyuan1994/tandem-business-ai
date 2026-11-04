"""Erasure hardening after review: safe search paths, structural JSON redaction, records linked to the customer

Revision ID: 20261027_1500_erasure_hardening
Revises: 20261027_0900_admin_ops_core
Created (UTC): 2026-10-27

- Every SECURITY DEFINER function searches `pg_temp` last. By default PostgreSQL searches the session's temporary
  schema first, so the application role (which may create temporary tables) could otherwise shadow a table used by a
  function that runs with the owner's rights.
- `redact_jsonb` walks the JSON value instead of editing its text, so numbers, keys and ids are never changed.
- Records that belong to the erased customer (found through the customer's id and the ids of their appointments,
  conversations and proposals) lose all of their free text, not only the strings that match the current name.
- Stock movement notes and campaign revisions (both append-only) can be redacted by narrow, guarded functions.
- `erased` is a suppression reason, and a customer can have only one completed erasure.
"""

from __future__ import annotations

from alembic import op

revision = "20261027_1500_erasure_hardening"
down_revision = "20261027_0900_admin_ops_core"
branch_labels = None
depends_on = None

SAFE_PATH = "pg_catalog, public, pg_temp"

EARLIER_DEFINERS = (
    "public_business_by_slug(text)",
    "pending_invitations_for_email(text)",
    "businesses_with_due_proposals(timestamptz)",
    "businesses_with_expired_holds(timestamptz)",
    "erase_redact_consent(uuid, uuid)",
)

REDACT_JSONB = """
create or replace function redact_jsonb(j jsonb, needles text[]) returns jsonb language plpgsql immutable as $$
begin
  if j is null then return null; end if;
  case jsonb_typeof(j)
    when 'string' then return to_jsonb(redact_text(j #>> '{}', needles));
    when 'object' then
      return coalesce((select jsonb_object_agg(key, redact_jsonb(value, needles)) from jsonb_each(j)), '{}'::jsonb);
    when 'array' then
      return coalesce((select jsonb_agg(redact_jsonb(value, needles) order by ord)
                         from jsonb_array_elements(j) with ordinality as e(value, ord)), '[]'::jsonb);
    else return j;
  end case;
end $$
"""

# Linked records keep their structure, numbers, ids and timestamps; every other string becomes "[erased]".
SCRUB_JSONB = r"""
create function scrub_jsonb(j jsonb) returns jsonb language plpgsql immutable as $$
declare s text;
begin
  if j is null then return null; end if;
  case jsonb_typeof(j)
    when 'string' then
      s := j #>> '{}';
      if s ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
         or s ~ '^\d{4}-\d{2}-\d{2}([T ][0-9:.]+([+-]\d{2}:?\d{2}|Z)?)?$' then
        return j;
      end if;
      return to_jsonb('[erased]'::text);
    when 'object' then
      return coalesce((select jsonb_object_agg(key, scrub_jsonb(value)) from jsonb_each(j)), '{}'::jsonb);
    when 'array' then
      return coalesce((select jsonb_agg(scrub_jsonb(value) order by ord)
                         from jsonb_array_elements(j) with ordinality as e(value, ord)), '[]'::jsonb);
    else return j;
  end case;
end $$
"""

GUARD = """
  if p_business is distinct from nullif(current_setting('app.business_id', true), '')::uuid then
    raise exception 'erasure is limited to the business of the current session';
  end if;
"""

# Proposals whose payload names one of the linked ids (customer, appointment, conversation, ...).
LINKED_PROPOSALS = """
  select distinct r.proposal_id from public.proposal_revisions r
   where r.business_id = p_business
     and exists (select 1 from unnest(p_ids) as i(id) where r.payload::text like '%' || i.id::text || '%')
"""

ERASE_PROPOSALS = f"""
create function erase_redact_proposals(p_business uuid, p_needles text[], p_ids uuid[]) returns bigint
language plpgsql security definer set search_path = {SAFE_PATH} as $$
declare c1 bigint; c2 bigint; c3 bigint; linked uuid[];
begin
{GUARD}
  linked := array(select * from ({LINKED_PROPOSALS}) l);
  update public.action_proposals
     set title = case when id = any(linked) then '[erased]' else redact_text(title, p_needles) end
   where business_id = p_business
     and title is distinct from (case when id = any(linked) then '[erased]' else redact_text(title, p_needles) end);
  get diagnostics c1 = row_count;
  update public.proposal_revisions r
     set payload = case when r.proposal_id = any(linked) then scrub_jsonb(r.payload) else redact_jsonb(r.payload, p_needles) end,
         summary = case when r.proposal_id = any(linked) then '[erased]' else redact_text(r.summary, p_needles) end,
         evidence = case when r.proposal_id = any(linked) then scrub_jsonb(r.evidence) else redact_jsonb(r.evidence, p_needles) end,
         preview = case when r.proposal_id = any(linked) then scrub_jsonb(r.preview) else redact_jsonb(r.preview, p_needles) end
   where r.business_id = p_business
     and (r.proposal_id = any(linked)
          or r.payload is distinct from redact_jsonb(r.payload, p_needles)
          or r.summary is distinct from redact_text(r.summary, p_needles)
          or r.evidence is distinct from redact_jsonb(r.evidence, p_needles)
          or r.preview is distinct from redact_jsonb(r.preview, p_needles));
  get diagnostics c2 = row_count;
  update public.approval_decisions
     set comment = case when proposal_id = any(linked) then '[erased]' else redact_text(comment, p_needles) end
   where business_id = p_business and comment is not null
     and comment is distinct from (case when proposal_id = any(linked) then '[erased]' else redact_text(comment, p_needles) end);
  get diagnostics c3 = row_count;
  return c1 + c2 + c3;
end $$
"""

ERASE_AUDIT = f"""
create function erase_redact_audit(p_business uuid, p_needles text[], p_ids uuid[]) returns bigint
language plpgsql security definer set search_path = {SAFE_PATH} as $$
declare c bigint; linked uuid[];
begin
{GUARD}
  linked := p_ids || array(select * from ({LINKED_PROPOSALS}) l);
  update public.audit_events
     set summary = case when target_id = any(linked) or proposal_id = any(linked) then '[erased]'
                        else redact_text(summary, p_needles) end,
         changes = case when target_id = any(linked) or proposal_id = any(linked) then scrub_jsonb(changes)
                        else redact_jsonb(changes, p_needles) end
   where business_id = p_business
     and ((target_id = any(linked) or proposal_id = any(linked)) and (summary <> '[erased]' or changes is distinct from scrub_jsonb(changes))
          or summary is distinct from redact_text(summary, p_needles)
          or changes is distinct from redact_jsonb(changes, p_needles));
  get diagnostics c = row_count;
  return c;
end $$
"""

ERASE_STOCK_NOTES = f"""
create function erase_redact_stock_notes(p_business uuid, p_needles text[], p_ids uuid[]) returns bigint
language plpgsql security definer set search_path = {SAFE_PATH} as $$
declare c bigint;
begin
{GUARD}
  update public.stock_movements
     set note = case when ref_id = any(p_ids) then '[erased]' else redact_text(note, p_needles) end
   where business_id = p_business and note is not null
     and note is distinct from (case when ref_id = any(p_ids) then '[erased]' else redact_text(note, p_needles) end);
  get diagnostics c = row_count;
  return c;
end $$
"""

ERASE_CAMPAIGN_REVISIONS = f"""
create function erase_redact_campaign_revisions(p_business uuid, p_needles text[]) returns bigint
language plpgsql security definer set search_path = {SAFE_PATH} as $$
declare c bigint;
begin
{GUARD}
  update public.campaign_revisions
     set subject = redact_text(subject, p_needles), body_markdown = redact_text(body_markdown, p_needles)
   where business_id = p_business
     and (subject is distinct from redact_text(subject, p_needles)
          or body_markdown is distinct from redact_text(body_markdown, p_needles));
  get diagnostics c = row_count;
  return c;
end $$
"""

NEW_FUNCTIONS = {
    "erase_redact_proposals(uuid, text[], uuid[])": ERASE_PROPOSALS,
    "erase_redact_audit(uuid, text[], uuid[])": ERASE_AUDIT,
    "erase_redact_stock_notes(uuid, text[], uuid[])": ERASE_STOCK_NOTES,
    "erase_redact_campaign_revisions(uuid, text[])": ERASE_CAMPAIGN_REVISIONS,
}


def upgrade() -> None:
    for fn in EARLIER_DEFINERS:
        op.execute(f"alter function {fn} set search_path = {SAFE_PATH}")
    op.execute("drop function erase_redact_audit(uuid, text[])")
    op.execute("drop function erase_redact_proposals(uuid, text[])")
    op.execute(REDACT_JSONB)
    op.execute(SCRUB_JSONB)
    for signature, body in NEW_FUNCTIONS.items():
        op.execute(body)
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to smb_app")
    op.execute("revoke all on function erase_redact_consent(uuid, uuid) from public")
    op.execute("grant execute on function erase_redact_consent(uuid, uuid) to smb_app")
    op.execute("alter table suppressions drop constraint ck_suppressions_reason")
    op.execute(
        "alter table suppressions add constraint ck_suppressions_reason "
        "check (reason in ('unsubscribed','bounced','complaint','manual','erased'))"
    )
    op.execute(
        "create unique index ux_erasure_requests_completed on erasure_requests (business_id, customer_id) "
        "where status = 'completed'"
    )


def downgrade() -> None:
    op.execute("drop index if exists ux_erasure_requests_completed")
    op.execute("alter table suppressions drop constraint ck_suppressions_reason")
    op.execute(
        "alter table suppressions add constraint ck_suppressions_reason "
        "check (reason in ('unsubscribed','bounced','complaint','manual'))"
    )
    for signature in NEW_FUNCTIONS:
        op.execute(f"drop function if exists {signature}")
    op.execute("drop function if exists scrub_jsonb(jsonb)")
    # The previous text-based versions are restored by re-running their definitions from 20261027_0900.
    from importlib import import_module

    previous = import_module("migrations.versions.20261027_0900_admin_ops_core")
    op.execute("drop function if exists redact_jsonb(jsonb, text[])")
    for statement in (previous.REDACT_JSONB, previous.ERASE_AUDIT, previous.ERASE_PROPOSALS):
        op.execute(statement)
    for fn in EARLIER_DEFINERS:
        op.execute(f"alter function {fn} set search_path = public")
