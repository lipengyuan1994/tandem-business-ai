"""Idempotency-Key support for creating POSTs (04 API conventions).

Usage inside an endpoint's transaction:
    cached = idempotency.lookup(db, ctx, key, body)
    if cached: return JSONResponse(cached.body, status_code=cached.status)
    ... create ...
    idempotency.store(db, ctx, key, body, 201, response_dict)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.platform import clock
from app.platform.canonical import sha256_hex, to_jsonable
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid
from app.platform.models import IdempotencyKey

TTL = timedelta(hours=24)


@dataclass(frozen=True)
class CachedResponse:
    status: int
    body: dict[str, Any]


def _validate(key: str) -> None:
    if not 8 <= len(key) <= 200:
        raise Invalid("platform.invalid_idempotency_key")


def lookup(db: Session, ctx: Context, key: str, request_body: Any) -> CachedResponse | None:
    _validate(key)
    # A row lock cannot protect the first use of an absent key. Hold this through creation and store().
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"idempotency:{ctx.business_id}:{key}"},
    )
    row = db.scalar(
        select(IdempotencyKey)
        .where(IdempotencyKey.business_id == ctx.business_id, IdempotencyKey.key == key)
        .with_for_update()
    )
    if row is None:
        return None
    if row.created_at < clock.now() - TTL:
        db.execute(
            delete(IdempotencyKey).where(IdempotencyKey.business_id == ctx.business_id, IdempotencyKey.key == key)
        )
        return None
    if row.request_hash != sha256_hex(request_body):
        raise Conflict("platform.idempotency_key_reused")
    return CachedResponse(row.response_status, row.response_body)


def store(db: Session, ctx: Context, key: str, request_body: Any, status: int, response_body: Any) -> None:
    _validate(key)
    db.execute(
        pg_insert(IdempotencyKey)
        .values(
            business_id=ctx.business_id,
            key=key,
            user_id=ctx.user_id,
            request_hash=sha256_hex(request_body),
            response_status=status,
            response_body=to_jsonable(response_body),
            created_at=clock.now(),  # same clock as the expiry check in lookup()
        )
        .on_conflict_do_nothing()
    )
