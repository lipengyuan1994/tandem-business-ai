"""Stable module boundary. Public retrieval always filters visibility before ranking."""

import uuid

from sqlalchemy.orm import Session

from app.platform.context import Context

from .schemas import SearchHit
from .service import search_public, search_staff, suggest

__all__ = ["SearchHit", "search_public", "search_staff", "suggest"]


def dictation_names(db: Session, ctx: Context, entity_ids: set[uuid.UUID]) -> list[str]:
    """Exact on-screen article titles only; never article bodies or visitor questions."""
    from sqlalchemy import select

    from .models import Article

    ctx.require("conversations.thread.read")
    return list(
        db.scalars(
            select(Article.title).where(Article.business_id == ctx.business_id, Article.id.in_(entity_ids)).limit(10)
        )
    )
