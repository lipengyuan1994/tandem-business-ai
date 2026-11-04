"""Migrations: full downgrade and re-upgrade works on the test database."""

from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from app.platform.testing.plugin import TEST_DB_OWNER, _create_test_tables

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _cfg() -> Config:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    cfg.attributes["url"] = TEST_DB_OWNER
    return cfg


def test_downgrade_to_base_and_upgrade_again(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("drop table if exists test_items"))
    command.downgrade(_cfg(), "base")
    with owner_engine.connect() as conn:
        left = conn.execute(
            text("select tablename from pg_tables where schemaname='public' and tablename <> 'alembic_version'")
        )
        assert list(left) == []
    command.upgrade(_cfg(), "head")
    _create_test_tables(owner_engine)
