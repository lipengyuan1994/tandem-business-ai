"""Public A0 rule and Activity DTOs (shared with the AI router)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.platform.registry import DeliveryState

Level = Literal["off", "draft", "ask", "act_notify", "act"]


class RulePut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: str = Field(max_length=100)
    level: Level
    shadow: bool = True
    daily_cap: int = Field(default=10, ge=0, le=2147483647)
    active: bool = True
    version: int = Field(ge=0)


class ShadowSummary(BaseModel):
    would_act: int = 0
    acted: int = 0
    fell_back: int = 0


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID | None = None
    action_type: str
    level: Level = "ask"
    shadow: bool = True
    daily_cap: int = 10
    active: bool = True
    version: int = 0
    granted_by: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    summary: ShadowSummary = Field(default_factory=ShadowSummary)


class RulesOut(BaseModel):
    paused: bool
    items: list[RuleOut]


class PauseOut(BaseModel):
    paused: bool


class ActivityOut(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    rule_id: uuid.UUID
    rule_version: int
    outcome: Literal["acted", "would_act", "fell_back", "blocked"]
    reasons: list[str]
    created_at: datetime
    title: str
    action_type: str
    granted_by: uuid.UUID | None
    granted_by_name: str
    status: str
    failure_code: str | None
    reorder_draft_id: uuid.UUID | None
    result_link: str | None
    delivery_state: DeliveryState = "none"


class ActivityPage(BaseModel):
    items: list[ActivityOut]
    next_cursor: str | None
