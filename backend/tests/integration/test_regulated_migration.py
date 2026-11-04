"""Upgrade protects existing restrictions and backfills the original authentication time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.platform.ids import new_id
from app.platform.testing.plugin import TEST_DB_OWNER


def test_hardening_upgrade_backfills_existing_restrictions_and_authentication(
    owner_engine: Engine, make_business: Any
) -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB_OWNER
    business = make_business()
    user, person, conversation, message, outbox, unrelated = (new_id() for _ in range(6))
    now = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)
    authenticated = now - timedelta(hours=5)
    try:
        command.downgrade(cfg, "20261028_1100_regulated_profile")
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "insert into users(id, oidc_subject, email, display_name) values (:i, 'migration-example', "
                    "'migration@example.com', 'Migration Example')"
                ),
                {"i": user},
            )
            conn.execute(
                text(
                    "insert into sessions(id, token_hash, user_id, csrf_token_hash, created_at, last_seen_at, "
                    "expires_at) values (:i, :token, :u, :csrf, :auth, :now, :expiry)"
                ),
                {
                    "i": new_id(),
                    "token": "a" * 64,
                    "csrf": "b" * 64,
                    "u": user,
                    "auth": authenticated,
                    "now": now,
                    "expiry": now + timedelta(hours=7),
                },
            )
            conn.execute(
                text(
                    "insert into customers(id, business_id, kind, display_name, source, status) values "
                    "(:i,:b,'person','Migration Example','staff','restricted')"
                ),
                {"i": person, "b": business},
            )
            conn.execute(
                text(
                    "insert into conversations(id,business_id,customer_id,channel,status,last_message_at) "
                    "values (:i,:b,:c,'phone_log','open',:now)"
                ),
                {"i": conversation, "b": business, "c": person, "now": now},
            )
            conn.execute(
                text(
                    "insert into "
                    "messages(id,business_id,conversation_id,direction,author_kind,body,outbox_message_id) "
                    "values (:i,:b,:c,'outbound','staff','Retain this text',:o)"
                ),
                {"i": message, "b": business, "c": conversation, "o": outbox},
            )
            for oid, related in ((outbox, message), (unrelated, None)):
                conn.execute(
                    text(
                        "insert into "
                        "outbox_messages(id,business_id,channel,recipient,template_key,variables,"
                        "idempotency_key,status,related_id) values (:i,:b,'simulated',"
                        "'migration@example.com','test_message','{}',:key,'pending',:r)"
                    ),
                    {"i": oid, "b": business, "key": str(oid), "r": related},
                )
            conn.execute(
                text(
                    "insert into idempotency_keys(business_id,key,request_hash,response_status,response_body) "
                    "values (:b,'migration-customer-cache',:hash,201,jsonb_build_object('id',cast(:c as text)))"
                ),
                {"b": business, "hash": "c" * 64, "c": person},
            )
        command.upgrade(cfg, "head")
        with owner_engine.connect() as conn:
            assert (
                conn.execute(text("select authenticated_at from sessions where user_id=:u"), {"u": user}).scalar_one()
                == authenticated
            )
            assert (
                conn.execute(
                    text("select restricted_at from conversations where id=:i"), {"i": conversation}
                ).scalar_one()
                is not None
            )
            row = conn.execute(
                text("select restricted_at, status, variables from outbox_messages where id=:i"), {"i": outbox}
            ).one()
            assert row.restricted_at is not None and row.status == "failed" and row.variables == {}
            assert (
                conn.execute(
                    text("select restricted_at from outbox_messages where id=:i"), {"i": unrelated}
                ).scalar_one()
                is None
            )
            assert (
                conn.execute(
                    text("select count(*) from idempotency_keys where business_id=:b"), {"b": business}
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(text("select body from messages where id=:i"), {"i": message}).scalar_one()
                == "Retain this text"
            )
        with pytest.raises(DBAPIError, match="cannot discard durable restrictions or authentication origin"):
            command.downgrade(cfg, "20261028_1100_regulated_profile")
    finally:
        command.upgrade(cfg, "head")
