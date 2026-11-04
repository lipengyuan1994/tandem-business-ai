"""Shared policy and IP budget for public conversation creation."""

from sqlalchemy.orm import Session

from app.modules.org import public as org
from app.platform import ratelimit
from app.platform.context import Context
from app.platform.errors import Forbidden


def require_public_intake(db: Session, ctx: Context) -> None:
    if not org.get_settings(db, ctx).public_chat_enabled:
        raise Forbidden("conversations.public_chat_disabled")


def limit_public_creation(db: Session, ip: str) -> None:
    ratelimit.hit(db, f"public-conversation-start:{ip}", limit=5, window_seconds=3600)
