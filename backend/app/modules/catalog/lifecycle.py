"""Export of the catalog module's data (M18)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.export import dump_models

from .models import CatalogItem, ServicePart


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    return dump_models(db, ctx, [CatalogItem, ServicePart])
