"""Populated rollback keeps erasure suppressions compatible with the old check constraint."""

from __future__ import annotations

from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from app.platform.ids import new_id
from app.platform.testing.plugin import TEST_DB_OWNER


def test_erased_suppression_survives_downgrade_to_old_constraint(owner_engine: Engine, make_business: Any) -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB_OWNER
    business = make_business()
    suppression = new_id()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "insert into suppressions(id,business_id,channel,value_normalized,reason) "
                "values (:i,:b,'email','erased@example.com','erased')"
            ),
            {"i": suppression, "b": business},
        )
    try:
        command.downgrade(cfg, "20261027_0900_admin_ops_core")
        with owner_engine.connect() as conn:
            assert conn.execute(
                text("select value_normalized,reason from suppressions where id=:i"), {"i": suppression}
            ).one() == ("erased@example.com", "manual")
    finally:
        command.upgrade(cfg, "head")


def test_upgrade_revokes_previously_erased_visitor_tokens(owner_engine: Engine, make_business: Any) -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB_OWNER
    business = make_business()
    erased, ordinary = new_id(), new_id()
    try:
        command.downgrade(cfg, "20261028_1200_regulated_hardening")
        with owner_engine.begin() as conn:
            for cid, name in ((erased, None), (ordinary, "Example visitor")):
                conn.execute(
                    text(
                        "insert into conversations(id,business_id,channel,status,last_message_at,visitor_name) "
                        "values (:i,:b,'web_chat','open',now(),:n)"
                    ),
                    {"i": cid, "b": business, "n": name},
                )
                conn.execute(
                    text(
                        "insert into messages(id,business_id,conversation_id,direction,author_kind,body) "
                        "values (:i,:b,:c,'inbound','visitor','[erased]')"
                    ),
                    {"i": new_id(), "b": business, "c": cid},
                )
                conn.execute(
                    text(
                        "insert into visitor_tokens(id,business_id,conversation_id,token_hash,expires_at) "
                        "values (:i,:b,:c,:h,now()+interval '7 days')"
                    ),
                    {"i": new_id(), "b": business, "c": cid, "h": cid.hex * 2},
                )
            # Old credentials could already have appended new text after a completed erasure.
            conn.execute(
                text(
                    "insert into messages(id,business_id,conversation_id,direction,author_kind,body) "
                    "values (:i,:b,:c,'inbound','visitor','Synthetic later text')"
                ),
                {"i": new_id(), "b": business, "c": erased},
            )
        command.upgrade(cfg, "head")
        with owner_engine.connect() as conn:
            assert conn.execute(
                text("select erased_at is not null from conversations where id=:i"), {"i": erased}
            ).scalar_one()
            assert conn.execute(
                text("select revoked_at is not null from visitor_tokens where conversation_id=:i"), {"i": erased}
            ).scalar_one()
            assert conn.execute(
                text("select erased_at is null from conversations where id=:i"), {"i": ordinary}
            ).scalar_one()
            assert conn.execute(
                text("select revoked_at is null from visitor_tokens where conversation_id=:i"), {"i": ordinary}
            ).scalar_one()
    finally:
        command.upgrade(cfg, "head")
