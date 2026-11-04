"""Small, safe building blocks for module repositories: business-scoped get, versioned update, cursor pages.

Every helper filters by `ctx.business_id` explicitly (row-level security is the second line of defence).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.errors import Conflict, NotFound
from app.platform.models import BusinessScopedBase
from app.platform.pagination import clamp_limit, decode_cursor, encode_cursor

PROTECTED = frozenset({"id", "business_id", "version", "created_at", "updated_at"})


def get_scoped[M: BusinessScopedBase](
    db: Session, ctx: Context, model: type[M], row_id: uuid.UUID, *, for_update: bool = False
) -> M | None:
    stmt = select(model).where(model.business_id == ctx.business_id, model.id == row_id)
    return db.scalar(stmt.with_for_update() if for_update else stmt)


def require_scoped[M: BusinessScopedBase](
    db: Session, ctx: Context, model: type[M], row_id: uuid.UUID, code: str, *, for_update: bool = False
) -> M:
    row = get_scoped(db, ctx, model, row_id, for_update=for_update)
    if row is None:
        raise NotFound(code)
    return row


def update_versioned[M: BusinessScopedBase](
    db: Session,
    ctx: Context,
    model: type[M],
    row_id: uuid.UUID,
    expected_version: int,
    changes: dict[str, Any],
    conflict_code: str,
) -> M:
    """UPDATE … WHERE version = expected; raises Conflict (409, current_version) or NotFound.

    Protected columns (id, business_id, version, timestamps) in `changes` are ignored.
    """
    changes = {k: v for k, v in changes.items() if k not in PROTECTED}
    stmt = (
        update(model)
        .where(model.business_id == ctx.business_id, model.id == row_id, model.version == expected_version)
        .values(**changes, version=model.version + 1, updated_at=func.now())
        .returning(model)
    )
    row = db.scalar(stmt)
    if row is not None:
        return row
    current = get_scoped(db, ctx, model, row_id)
    if current is None:
        raise NotFound(conflict_code.replace("version_conflict", "not_found"))
    raise Conflict(conflict_code, current_version=current.version)


def page[M: BusinessScopedBase](
    db: Session, stmt: Select[Any], model: type[M], limit: int, cursor: str | None
) -> tuple[list[Any], str | None]:
    """Newest-first cursor page over (created_at, id)."""
    limit = clamp_limit(limit)
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(or_(model.created_at < ts, and_(model.created_at == ts, model.id < rid)))
    rows = list(db.scalars(stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)))
    items = rows[:limit]
    return items, (encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit and items else None)


def rows_affected(result: Any) -> int:
    """Number of rows an UPDATE or DELETE touched (SQLAlchemy types `execute()` as a plain Result without it)."""
    return int(getattr(result, "rowcount", 0) or 0)
