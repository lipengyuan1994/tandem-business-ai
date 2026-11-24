"""pytest plugin (loaded via `-p app.platform.testing.plugin`): real PostgreSQL test database, truncation
between tests, registry and context helpers. Requires `make test-db` (localhost:5433)."""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text

TEST_DB_APP = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://smb_app:smb_app_dev@localhost:5433/smb")
TEST_DB_OWNER = os.environ.get(
    "TEST_DATABASE_MIGRATION_URL", "postgresql+psycopg://smb_owner:smb_owner_dev@localhost:5433/smb"
)


def pytest_configure(config: pytest.Config) -> None:
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = TEST_DB_APP
    os.environ["DATABASE_MIGRATION_URL"] = TEST_DB_OWNER
    os.environ.setdefault("APP_SECRET_KEY", secrets.token_hex(32))  # random per test run
    os.environ["EGRESS_ALLOWLIST"] = "api.example-provider.test"
    from app.platform.config import get_settings

    get_settings.cache_clear()


@pytest.fixture(scope="session")
def owner_engine() -> Iterator[Engine]:
    """Owner-role engine (bypasses RLS). Only for test setup and assertions, never for code under test."""
    engine = create_engine(TEST_DB_OWNER)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database(owner_engine: Engine) -> Iterator[None]:
    from alembic import command
    from alembic.config import Config

    from app.platform.db import configure_engine

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    cfg.attributes["url"] = TEST_DB_OWNER
    command.upgrade(cfg, "head")
    _create_test_tables(owner_engine)
    configure_engine(TEST_DB_APP)
    yield


def _create_test_tables(engine: Engine) -> None:
    """A business-scoped scratch table used by platform tests (RLS, audit rollback, jobs)."""
    with engine.begin() as conn:
        conn.execute(text("drop table if exists test_items"))
        conn.execute(
            text(
                """create table test_items (id uuid primary key, business_id uuid not null references businesses(id),
                                            name text not null, version int not null default 1)"""
            )
        )
        conn.execute(text("alter table test_items enable row level security"))
        conn.execute(text("alter table test_items force row level security"))
        conn.execute(
            text(
                """create policy business_isolation on test_items
                   using (business_id = nullif(current_setting('app.business_id', true), '')::uuid)
                   with check (business_id = nullif(current_setting('app.business_id', true), '')::uuid)"""
            )
        )
        conn.execute(text("grant select, insert, update, delete on test_items to smb_app"))


@pytest.fixture(autouse=True)
def _clean_tables(owner_engine: Engine) -> Iterator[None]:
    yield
    with owner_engine.begin() as conn:
        tables = conn.execute(
            text("select tablename from pg_tables where schemaname = 'public' and tablename <> 'alembic_version'")
        ).scalars()
        names = ", ".join(f'"{t}"' for t in tables)
        if names:
            conn.execute(text(f"truncate {names} restart identity cascade"))


@pytest.fixture(autouse=True)
def _frozen_clock_reset() -> Iterator[None]:
    from app.platform import clock

    yield
    clock.reset_clock()


@pytest.fixture
def freeze_time() -> Any:
    """freeze_time(datetime) pins platform.clock.now() for the rest of the test."""
    from app.platform import clock

    def _freeze(at: datetime) -> None:
        clock.set_clock(lambda: at)

    return _freeze


@pytest.fixture
def make_business(owner_engine: Engine) -> Any:
    """make_business(slug=..., timezone=...) -> UUID, inserted with the owner role."""
    from app.platform.ids import new_id

    def _make(slug: str | None = None, timezone: str = "America/Denver", currency: str = "USD") -> uuid.UUID:
        business_id = new_id()
        slug = slug or f"biz-{business_id.hex[-8:]}"
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "insert into businesses (id, slug, name, timezone, currency) values (:id, :slug, :name, :tz, :cur)"
                ),
                {"id": business_id, "slug": slug, "name": slug.title(), "tz": timezone, "cur": currency},
            )
        return business_id

    return _make


@pytest.fixture
def business(make_business: Any) -> uuid.UUID:
    return make_business("example-cycle-works")  # type: ignore[no-any-return]


@pytest.fixture
def other_business(make_business: Any) -> uuid.UUID:
    return make_business("example-garden-store", timezone="Europe/London", currency="GBP")  # type: ignore[no-any-return]


@pytest.fixture
def registry() -> Iterator[Any]:
    """A fresh, unfrozen-then-frozen registry with all installed modules; tests may add hooks before freezing
    by using `registry_builder` instead."""
    from app.main import build_registry

    yield build_registry()


@pytest.fixture
def registry_builder() -> Any:
    """registry_builder(extra_hook) -> Registry. `extra_hook(reg)` runs before freeze (add test subscribers etc.)."""
    import types

    from app.main import build_registry

    def _build(hook: Any = None) -> Any:
        extra = []
        if hook is not None:
            module = types.ModuleType("test_hooks")
            module.register = hook  # type: ignore[attr-defined]
            extra.append(module)
        return build_registry(extra)

    return _build
