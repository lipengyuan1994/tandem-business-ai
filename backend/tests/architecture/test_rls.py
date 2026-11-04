"""Every table with business_id has row-level security (FORCE + policy), except the declared exemptions."""

from __future__ import annotations

from sqlalchemy import Engine, text

# Platform queue tables are claimed across businesses by the worker (05-data-model.md). Changing this list
# is a visible design decision.
RLS_EXEMPT = {"jobs", "domain_events", "event_deliveries", "outbox_messages", "rate_limits"}
# Global identity tables have no business_id at all (users, sessions, oidc_login_states) and are not listed.

QUERY = text(
    """
    select c.relname, c.relrowsecurity, c.relforcerowsecurity,
           exists (select 1 from pg_policies p where p.tablename = c.relname) as has_policy
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r'
       and (c.relname = 'businesses' or exists (
             select 1 from information_schema.columns col
              where col.table_schema = 'public' and col.table_name = c.relname and col.column_name = 'business_id'))
    """
)


def tables_missing_rls(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(QUERY).all()
    return sorted(
        r.relname
        for r in rows
        if r.relname not in RLS_EXEMPT and not (r.relrowsecurity and r.relforcerowsecurity and r.has_policy)
    )


def test_every_business_table_has_rls(owner_engine: Engine) -> None:
    assert tables_missing_rls(owner_engine) == []


def test_checker_catches_table_without_rls(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("create table arch_probe (id uuid primary key, business_id uuid)"))
    try:
        assert tables_missing_rls(owner_engine) == ["arch_probe"]
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("drop table arch_probe"))
