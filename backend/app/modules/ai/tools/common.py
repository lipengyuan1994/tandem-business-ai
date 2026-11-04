"""Small helpers shared by the tool files: compact JSON values, list caps, contact redaction, evidence.

Read tools return little: money as `{"minor", "currency"}`, quantities as strings, times in UTC (`…Z`), lists cut to
20 items. Free text that reaches the model (messages, timeline entries, supplier notes) has e-mail addresses and phone
numbers replaced, because customer contact values stay server-side (contracts §10, AI-06).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from app.modules.approvals.public import Evidence

from .base import RunState

MAX_LIST_ITEMS = 20
MAX_EVIDENCE = 10
MAX_EXCERPT_CHARS = 500
EMAIL_MASK = "[email hidden]"
PHONE_MASK = "[phone hidden]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A phone number: an optional "+", then digits with spaces, dots, dashes or brackets between them. Dates and times
# ("2026-11-10", "10:00") have too few digits to match; a number counts as a phone with 9+ digits, or 7+ after a "+".
_PHONE = re.compile(r"(?<![\w+])(\+?\(?\d[\d\s().-]{5,}\d)(?![\w])")


class ToolArgs(BaseModel):
    """Base of every tool's arguments: unknown fields are refused (and `business_id` is never one of them)."""

    model_config = ConfigDict(extra="forbid")


class NoArgs(ToolArgs):
    pass


def money(minor: int | None, currency: str) -> dict[str, Any] | None:
    return None if minor is None else {"minor": int(minor), "currency": currency}


def qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return "0" if value == 0 else format(value.normalize(), "f")


def utc(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def local_label(value: datetime, timezone: str) -> str:
    """Same wording as scheduling's slot labels, e.g. "Tue 10 Nov 11:45 MST"."""
    local = value.astimezone(ZoneInfo(timezone))
    return f"{local:%a %d %b %H:%M} {local.tzname()}"


def ratio(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


def capped[T](items: Sequence[T], limit: int = MAX_LIST_ITEMS) -> tuple[list[T], bool]:
    """The first `limit` items and whether more were cut."""
    return list(items[:limit]), len(items) > limit


def listing(key: str, rows: Sequence[dict[str, Any]], limit: int = MAX_LIST_ITEMS) -> dict[str, Any]:
    kept, more = capped(rows, limit)
    out: dict[str, Any] = {key: kept}
    if more:
        out["more"] = True
    return out


def _is_phone(match: re.Match[str]) -> bool:
    number: str = match.group(1)
    return sum(c.isdigit() for c in number) >= (7 if number.startswith("+") else 9)


def redact_contacts(text: str | None, limit: int | None = None) -> str | None:
    """Replace e-mail addresses and phone numbers; optionally cut to `limit` characters."""
    if text is None:
        return None
    text = _EMAIL.sub(EMAIL_MASK, text)
    text = _PHONE.sub(lambda m: PHONE_MASK if _is_phone(m) else m.group(0), text)
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def cite(state: RunState, tool: str, ref_id: uuid.UUID | None, data: Any) -> None:
    """Remember a tool result worth citing in a proposal (RunState.evidence keeps the newest 10)."""
    excerpt = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(excerpt) > MAX_EXCERPT_CHARS:
        excerpt = excerpt[: MAX_EXCERPT_CHARS - 1] + "…"
    state.evidence.append(Evidence(kind="tool_result", ref_type=tool, ref_id=ref_id, excerpt=excerpt))
    del state.evidence[:-MAX_EVIDENCE]
