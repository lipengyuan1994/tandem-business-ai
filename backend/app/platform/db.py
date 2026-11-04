"""Database engine, sessions and tenancy-aware transactions (05-data-model.md, 04 code patterns).

Rule: transactions are opened in endpoints and job handlers, never inside services.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.platform.config import get_settings
from app.platform.context import Context

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def configure_engine(url: str | None = None, **kwargs: object) -> Engine:
    """Create (or replace) the global engine. Tests call this with the test database URL."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        hide_parameters=True,
        **kwargs,
    )
    _session_factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    return _engine if _engine is not None else configure_engine()


def _factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure_engine()
    assert _session_factory is not None
    return _session_factory


SET_TENANT = text("select set_config('app.business_id', :b, true), set_config('app.user_id', :u, true)")


@contextmanager
def transaction(ctx: Context) -> Iterator[Session]:
    """One transaction with row-level-security variables set (transaction-local). Commits on clean exit."""
    with _factory()() as db, db.begin():
        db.execute(SET_TENANT, {"b": str(ctx.business_id or ""), "u": str(ctx.user_id or "")})
        yield db


@contextmanager
def system_transaction() -> Iterator[Session]:
    """Transaction with no tenant set: only platform queue tables (exempt from RLS) are visible."""
    with _factory()() as db, db.begin():
        db.execute(SET_TENANT, {"b": "", "u": ""})
        yield db
