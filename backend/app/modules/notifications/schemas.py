from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class OutboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    channel: str
    template_key: str
    related_type: str | None
    related_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    status: Literal["pending", "sending", "delivered", "failed", "unknown"]
    attempts: int
    provider_message_id: str | None
    last_error: str | None
    delivered_at: datetime | None
    resolution: str | None
    created_at: datetime

    @property
    def simulated(self) -> bool:
        return self.channel == "simulated"


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: Literal["delivered", "retry"]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    title: str
    body: str = ""
    link: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread: int
