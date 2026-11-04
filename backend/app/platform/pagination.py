"""Cursor pagination: opaque base64 of (created_at, id), ordered newest first."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.platform.errors import Invalid

MAX_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts, rid = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(ts), UUID(rid)
    except (ValueError, TypeError) as exc:
        raise Invalid("platform.invalid_cursor") from exc


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))
