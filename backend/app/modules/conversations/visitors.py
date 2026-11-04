"""Visitor token issue/resolution; kept separate from staff conversation mutations."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform import clock, crud
from app.platform.context import Context
from app.platform.errors import Forbidden, NotFound
from app.platform.permissions import hash_token

from .models import Conversation, VisitorToken

VISITOR_TTL = timedelta(days=7)


def issue_visitor_token(db: Session, ctx: Context, conversation_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    assert ctx.business_id is not None
    db.add(
        VisitorToken(
            business_id=ctx.business_id,
            conversation_id=conversation_id,
            token_hash=hash_token(token),
            expires_at=clock.now() + VISITOR_TTL,
        )
    )
    db.flush()
    return token


def visitor_conversation(db: Session, ctx: Context, token: str | None) -> Conversation:
    """The one conversation this visitor token grants. Conversation ids alone never grant access."""
    if not token:
        raise Forbidden("conversations.visitor_token_invalid")
    row = db.scalar(
        select(VisitorToken).where(
            VisitorToken.business_id == ctx.business_id, VisitorToken.token_hash == hash_token(token)
        )
    )
    if row is None or row.revoked_at is not None or row.expires_at < clock.now():
        raise Forbidden("conversations.visitor_token_invalid")
    conv = crud.get_scoped(db, ctx, Conversation, row.conversation_id)
    if conv is None:
        raise NotFound("conversations.not_found")
    if conv.erased_at is not None:
        raise Forbidden("conversations.visitor_token_invalid")
    return conv


def set_visitor_cookie(response: Response, business_slug: str, token: str) -> None:
    response.set_cookie(
        "smb_visitor",
        token,
        max_age=int(VISITOR_TTL.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path=f"/api/v1/public/{business_slug}/",
    )
