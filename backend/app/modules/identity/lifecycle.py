"""Export of the identity module's data for one business (M18): who works here and with which roles."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.canonical import to_jsonable
from app.platform.context import Context
from app.platform.export import dump_models

from .models import Invitation, Membership, RoleGrant, User


def export(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    out = dump_models(db, ctx, [Membership, RoleGrant, Invitation], exclude={"token_hash"})
    members = select(Membership.user_id).where(Membership.business_id == ctx.business_id)
    out["users"] = [
        {"id": to_jsonable(u.id), "email": u.email, "display_name": u.display_name, "status": u.status}
        for u in db.scalars(select(User).where(User.id.in_(members)))
    ]
    return out
