"""Data export helpers (M18): every module dumps its own tables for one business; admin_ops zips the result."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.platform.canonical import to_jsonable
from app.platform.context import Context
from app.platform.models import Base

BATCH = 1000


def dump_models(
    db: Session, ctx: Context, models: Iterable[type[Base]], exclude: Iterable[str] = ()
) -> dict[str, list[dict[str, Any]]]:
    """All rows of the business for each model, as JSON-able dicts. `exclude` drops columns such as token hashes."""
    skip = set(exclude)
    out: dict[str, list[dict[str, Any]]] = {}
    for model in models:
        columns = [c.key for c in inspect(model).mapper.column_attrs if c.key not in skip]
        # Every row, streamed in batches (no cap: an export that silently drops rows would not be a full export).
        stmt = (
            select(model)
            .where(model.business_id == ctx.business_id)  # type: ignore[attr-defined]
            .execution_options(yield_per=BATCH)
        )
        out[str(model.__tablename__)] = [{c: to_jsonable(getattr(row, c)) for c in columns} for row in db.scalars(stmt)]
    return out
