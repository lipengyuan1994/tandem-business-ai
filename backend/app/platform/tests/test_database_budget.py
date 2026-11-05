from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.platform.config import Settings, get_settings
from app.platform.db import configure_engine, system_transaction


@pytest.mark.parametrize("process,size,overflow", [("api", 4, 2), ("worker", 6, 0)])
def test_process_pool_and_postgres_session_timeouts(process: Any, size: int, overflow: int) -> None:
    try:
        engine = configure_engine(process=process)
        assert engine.pool.size() == size  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == overflow  # type: ignore[attr-defined]
        with system_transaction() as db:
            assert db.scalar(text("show statement_timeout")) == "30s"
            assert db.scalar(text("show idle_in_transaction_session_timeout")) == "1min"
    finally:
        configure_engine()


def test_worker_settings_reserve_capacity() -> None:
    settings = get_settings()
    with pytest.raises(ValidationError, match="WORKER_AI_CONCURRENCY"):
        Settings(
            _env_file=None,
            database_url=settings.database_url,
            app_secret_key=settings.app_secret_key,
            worker_concurrency=2,
            worker_ai_concurrency=2,
        )
    assert settings.worker_shutdown_grace_seconds == 100


def test_timeouts_apply_after_reconnect_and_cancel_queries(monkeypatch: Any) -> None:
    from sqlalchemy.exc import DBAPIError

    settings = get_settings()
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 25)
    try:
        engine = configure_engine()
        for _ in range(2):
            with pytest.raises(DBAPIError, match="statement timeout"):
                with system_transaction() as db:
                    db.execute(text("select pg_sleep(0.15)"))
            engine.dispose()
    finally:
        monkeypatch.setattr(settings, "db_statement_timeout_ms", 30_000)
        configure_engine()


def test_idle_transaction_is_terminated(monkeypatch: Any) -> None:
    import time

    from sqlalchemy.exc import DBAPIError

    settings = get_settings()
    monkeypatch.setattr(settings, "db_idle_transaction_timeout_ms", 50)
    try:
        configure_engine()
        with pytest.raises(DBAPIError):
            with system_transaction() as db:
                db.execute(text("select 1"))
                time.sleep(0.2)
                db.execute(text("select 1"))
    finally:
        monkeypatch.setattr(settings, "db_idle_transaction_timeout_ms", 60_000)
        configure_engine()


def test_owner_migration_connections_are_exempt(owner_engine: Any) -> None:
    with owner_engine.connect() as conn:
        assert conn.scalar(text("show statement_timeout")) == "0"
        assert conn.scalar(text("show idle_in_transaction_session_timeout")) == "0"
