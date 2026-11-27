"""Business export (without the relay credential). Payments rows hold no customer details, so erasure has
nothing to redact here (Stripe keeps its own records of the business's customers)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.export import ExportTables, export_tables, stream_models

from .models import PaymentConnection, PaymentEvent

SECRET_COLUMNS = ("relay_secret_sealed",)


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, [PaymentConnection, PaymentEvent], exclude=SECRET_COLUMNS)
    return export_tables(tables, streaming=streaming)
