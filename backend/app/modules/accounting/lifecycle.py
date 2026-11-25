"""Business export (without OAuth secrets) and redaction of erased customers' names in the sync log.

Erasing a customer in Tandem does not reach into QuickBooks: copies already sent there are the business's own
bookkeeping records and are corrected in QuickBooks itself.
"""

from __future__ import annotations

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import AccountingConnection, AccountingSyncRecord

SECRET_COLUMNS = ("access_token_sealed", "refresh_token_sealed", "oauth_state_hash")


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, [AccountingConnection, AccountingSyncRecord], exclude=SECRET_COLUMNS)
    return export_tables(tables, streaming=streaming)


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    counts: dict[str, int] = {}
    for column in (AccountingSyncRecord.label, AccountingSyncRecord.last_error):
        redacted = func.left(func.redact_text(column, target.needles), 500 if column.key == "last_error" else 200)
        result = db.execute(
            update(AccountingSyncRecord)
            .where(AccountingSyncRecord.business_id == ctx.business_id, column.is_distinct_from(redacted))
            .values({column.key: redacted, "version": AccountingSyncRecord.version + 1})
        )
        counts[f"accounting_sync_records_{column.key}_redacted"] = rows_affected(result)
    return counts
