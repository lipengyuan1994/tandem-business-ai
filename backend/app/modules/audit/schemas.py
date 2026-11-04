from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_kind: Literal["user", "assistant", "system", "visitor"]
    actor_id: uuid.UUID | None
    actor_name: str
    on_behalf_of_user_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    ai_run_id: uuid.UUID | None
    request_id: str | None
    summary: str
    changes: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


class AuditFilter(BaseModel):
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    actor_id: uuid.UUID | None = None
    actor_kind: str | None = None
    action_prefix: str | None = None
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
