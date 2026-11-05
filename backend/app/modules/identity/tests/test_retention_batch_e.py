from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from app.platform import clock, retention
from app.platform.context import system_context
from app.platform.db import system_transaction
from app.platform.ids import new_id


def test_expired_auth_rate_windows_and_idempotency_cleanup(business: UUID, owner_engine: Engine, registry: Any) -> None:
    from app.modules.identity.lifecycle import cleanup

    now = clock.now()
    user = new_id()
    with owner_engine.begin() as conn:
        conn.execute(
            text("insert into users (id, oidc_subject, email, display_name) values (:id, :s, :e, 'Test')"),
            {"id": user, "s": str(user), "e": "test@example.com"},
        )
        for offset in (-1, 1):
            at = now + timedelta(days=offset)
            conn.execute(
                text("""insert into sessions
                (id, token_hash, user_id, csrf_token_hash, last_seen_at, expires_at)
                values (:id, :hash, :user, :hash, :at, :at)"""),
                {"id": new_id(), "hash": str(new_id()), "user": user, "at": at},
            )
            conn.execute(
                text("""insert into oidc_login_states(state_hash, nonce, code_verifier, return_to, expires_at)
                values (:hash, 'n', 'v', '/', :at)"""),
                {"hash": str(new_id()), "at": at},
            )
            conn.execute(
                text("insert into rate_limits values (:key, :at, 1)"),
                {"key": str(offset), "at": now + timedelta(days=offset * 3)},
            )
            conn.execute(
                text("""insert into idempotency_keys
                (business_id, key, request_hash, response_status, response_body, created_at)
                values (:b, :key, 'h', 200, '{}', :at)"""),
                {"b": business, "key": str(offset), "at": now + timedelta(days=offset * 2)},
            )
    with system_transaction() as db:
        cleanup(db, system_context(None), {})
    retention.cleanup(system_context(None), {})
    with owner_engine.connect() as conn:
        for table in ("sessions", "oidc_login_states", "rate_limits", "idempotency_keys"):
            assert conn.scalar(text(f"select count(*) from {table}")) == 1  # noqa: S608 - fixed test tables
        assert conn.scalar(
            text("select prosecdef from pg_proc where oid='cleanup_expired_idempotency()'::regprocedure")
        )
    assert any(c.name == "platform-retention" and c.every_seconds == 86400 for c in registry.cron)
    assert any(c.name == "identity-retention" and c.every_seconds == 86400 for c in registry.cron)
