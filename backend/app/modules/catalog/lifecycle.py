"""Export of the catalog module's data (M18)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.export import ExportTables, export_tables, stream_models

from .models import CatalogItem, ServicePart


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, [CatalogItem, ServicePart])
    return export_tables(tables, streaming=streaming)
