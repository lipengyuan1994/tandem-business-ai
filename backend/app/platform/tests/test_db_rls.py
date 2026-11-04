"""P1-04: tenancy variables, row-level security, append-only audit."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.platform.db import get_engine, transaction
from app.platform.ids import new_id
from app.platform.testing.auth import ctx_for


def test_transaction_sets_rls_variables_and_resets_after_commit(business: Any) -> None:
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        assert db.execute(text("select current_setting('app.business_id', true)")).scalar() == str(business)
    with get_engine().connect() as conn:  # pooled connection must carry no tenant state
        assert conn.execute(text("select current_setting('app.business_id', true)")).scalar() in ("", None)


def test_rls_hides_other_business_rows(business: Any, other_business: Any) -> None:
    with transaction(ctx_for(business)) as db:
        db.execute(
            text("insert into test_items (id, business_id, name) values (:i, :b, 'mine')"),
            {"i": new_id(), "b": business},
        )
    with transaction(ctx_for(other_business)) as db:
        assert db.execute(text("select count(*) from test_items")).scalar() == 0
        # writing a row for another business is rejected by WITH CHECK
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(
                    text("insert into test_items (id, business_id, name) values (:i, :b, 'x')"),
                    {"i": new_id(), "b": business},
                )
    with transaction(ctx_for(None)) as db:  # no tenant -> fails closed
        assert db.execute(text("select count(*) from test_items")).scalar() == 0


def test_app_role_cannot_update_or_delete_audit_events(business: Any) -> None:
    from app.platform import audit

    ctx = ctx_for(business)
    with transaction(ctx) as db:
        audit.record(db, ctx, action="test.done", target_type="test", target_id=None, summary="x")
    for stmt in ("update audit_events set summary = 'y'", "delete from audit_events"):
        with pytest.raises(DBAPIError, match="permission denied"):
            with transaction(ctx) as db:
                db.execute(text(stmt))


def test_app_role_cannot_create_tables(business: Any) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        with transaction(ctx_for(business)) as db:
            db.execute(text("create table nope (id int)"))
