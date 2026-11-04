"""Audit log: export, and redaction of an erased person's name from event text (M18)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.export import dump_models
from app.platform.models import AuditEvent
from app.platform.registry import ErasureTarget


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    return dump_models(db, ctx, [AuditEvent])


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Events stay (who did what, when, to which id). Events about the person's records lose all their text; in other
    events the person's names, e-mail addresses and phone numbers are replaced."""
    changed = db.scalar(select(func.erase_redact_audit(ctx.business_id, target.needles, target.all_ids)))
    return {"audit_events_redacted": int(changed or 0)}
