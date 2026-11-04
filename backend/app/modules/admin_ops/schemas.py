from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class QueueStatus(BaseModel):
    counts: dict[str, int]
    oldest_queued_age_seconds: int | None
    dead_jobs: int
    dead_kinds: list[str]
    event_deliveries_dead: int


class OutboxStatus(BaseModel):
    counts: dict[str, int]
    needs_attention: int  # failed + unknown


class BackupStatus(BaseModel):
    available: bool
    result: str | None
    last_success: datetime | None
    age_seconds: int | None
    snapshot_id: str | None
    error: str | None


class EmailStatus(BaseModel):
    configured: bool
    host: str | None


class TestEmailIn(BaseModel):
    model_config = {"extra": "forbid"}
    to: EmailStr


class TestEmailOut(BaseModel):
    outbox_id: uuid.UUID
    status: str = "pending"


class SystemStatus(BaseModel):
    data_profile: str
    regulated_host_baa: str | None = None
    regulated_backup_baa: str | None = None
    version: str
    environment: str
    checked_at: datetime
    queue: QueueStatus
    outbox: OutboxStatus
    backup: BackupStatus
    email: EmailStatus
    ai_usage_today: None = None  # the AI layer is not installed yet


class ErasureIn(BaseModel):
    """`confirm_name` must equal the customer's current display name: a deliberate, typed confirmation."""

    model_config = {"extra": "forbid"}
    customer_id: uuid.UUID
    confirm_name: str


class ErasureOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    status: str
    report: dict[str, dict[str, int]]
    completed_at: datetime | None
