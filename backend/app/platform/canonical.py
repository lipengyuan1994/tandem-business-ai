"""Canonical JSON and hashing, used to bind approvals to exact proposal payloads (07-workflow-engine.md)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(repr(value)).normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime in canonical payload")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
