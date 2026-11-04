"""Alembic environment. Migrations run as the owner role (DATABASE_MIGRATION_URL)."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.main import load_models
from app.platform.config import get_settings
from app.platform.models import Base

load_models()
target_metadata = Base.metadata


def _url() -> str:
    url = (
        context.config.attributes.get("url")
        or os.environ.get("DATABASE_MIGRATION_URL")
        or get_settings().database_migration_url
    )
    if not url:
        raise RuntimeError("DATABASE_MIGRATION_URL is not set")
    return str(url)


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # Serialise concurrent runs (several containers starting at once): the second waits, then finds nothing to do.
        connection.execute(text("select pg_advisory_lock(727274)"))
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
