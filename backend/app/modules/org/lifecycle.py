"""Export of the org module's data (M18)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.platform.canonical import to_jsonable
from app.platform.context import Context
from app.platform.export import dump_models

from .models import Business, BusinessSettingsRow, Location, OpeningHours


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    out = dump_models(db, ctx, [Location, OpeningHours, BusinessSettingsRow])
    columns = [c.key for c in inspect(Business).mapper.column_attrs]
    rows = db.scalars(select(Business).where(Business.id == ctx.business_id))
    out["businesses"] = [{c: to_jsonable(getattr(b, c)) for c in columns} for b in rows]
    return out
