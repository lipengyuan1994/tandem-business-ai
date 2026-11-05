"""Export of the identity module's data for one business (M18): who works here and with which roles."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.canonical import to_jsonable
from app.platform.context import Context
from app.platform.export import BATCH, ExportTables, export_tables, stream_models

from .models import Invitation, Membership, RoleGrant, User


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    out = stream_models(db, ctx, [Membership, RoleGrant, Invitation], exclude={"token_hash"})
    members = select(Membership.user_id).where(Membership.business_id == ctx.business_id)
    out["users"] = (
        {"id": to_jsonable(u.id), "email": u.email, "display_name": u.display_name, "status": u.status}
        for u in db.scalars(select(User).where(User.id.in_(members)).execution_options(yield_per=BATCH))
    )
    return export_tables(out, streaming=streaming)


def cleanup(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    """Bounded expiry of authentication artifacts; membership and revocation audit stay."""
    from datetime import timedelta

    from app.platform import clock
    from app.platform.config import get_settings
    from app.platform.retention import BATCH, delete_batch, schedule_continuation

    sessions = delete_batch(
        db,
        "sessions",
        "expires_at < :now or revoked_at is not null or last_seen_at < :idle",
        "expires_at",
        {"now": clock.now(), "idle": clock.now() - timedelta(minutes=get_settings().staff_idle_minutes)},
    )
    states = delete_batch(db, "oidc_login_states", "expires_at < :now", "expires_at", {"now": clock.now()})
    if max(sessions, states) == BATCH:
        schedule_continuation(db, "identity.retention")
