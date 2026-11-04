"""Fixed-window rate limiting stored in PostgreSQL (public and login endpoints)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.db import system_transaction
from app.platform.errors import RateLimited

UPSERT = text(
    """
    insert into rate_limits (key, window_start, count) values (:key, :window, 1)
    on conflict (key, window_start) do update set count = rate_limits.count + 1
    returning count
    """
)


def hit(db: Session, key: str, limit: int, window_seconds: int) -> int:
    """Commit a hit independently of the caller's transaction, including rejected requests."""
    epoch = int(clock.now().timestamp())
    window = datetime.fromtimestamp(epoch - epoch % window_seconds, UTC)
    with system_transaction() as accounting:
        count = int(accounting.execute(UPSERT, {"key": key[:200], "window": window}).scalar_one())
    if count > limit:
        raise RateLimited("platform.rate_limited", retry_after_seconds=window_seconds - epoch % window_seconds)
    return count
