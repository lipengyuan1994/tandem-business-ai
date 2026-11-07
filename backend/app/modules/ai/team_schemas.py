"""AI team API contracts: metadata and completion evidence only."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.approvals import public as approvals
from app.platform.registry import DeliveryState


class AgentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    daily_cost_limit_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    version: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_patch(self) -> AgentPatch:
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("enabled must be a boolean")
        if not (self.model_fields_set & {"enabled", "daily_cost_limit_usd"}):
            raise ValueError("Supply at least one setting")
        return self


class TodayWork(BaseModel):
    runs: int = 0
    proposals_created: int = 0
    actions_executed_by_people: int = 0
    actions_executed_by_rules: int = 0
    handed_to_person: int = 0


class WaitingItems(BaseModel):
    count: int = 0
    oldest_at: datetime | None = None


class AgentOut(BaseModel):
    key: str
    name: str
    job: str
    triggers: list[str]
    switchable: bool
    enabled: bool
    version: int
    updated_by: UUID | None = None
    autonomy: list[approvals.RuleOut]
    today: TodayWork
    cost_today_micros: int
    daily_cost_limit_usd: Decimal | None
    waiting: WaitingItems
    last_activity_at: datetime | None
    status: Literal["on", "off", "paused", "limit_reached", "unavailable"]
    reason: str | None = None


class AgentsOut(BaseModel):
    items: list[AgentOut]


class AddressableAgent(BaseModel):
    key: str
    name: str
    enabled: bool


class AddressableAgentsOut(BaseModel):
    items: list[AddressableAgent]
    manager_enabled: bool
    manager_reason: str | None


class TeamActivityItem(BaseModel):
    id: UUID
    agent: str | None
    kind: str
    created_at: datetime
    status: str
    title: str
    proposal_id: UUID | None = None
    run_id: UUID | None = None
    action_type: str | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    depth: int | None = None
    cost_micros: int = 0
    reasons: list[str] = Field(default_factory=list)
    result_link: str | None = None
    delivery_state: DeliveryState = "none"


class TeamActivityPage(BaseModel):
    items: list[TeamActivityItem]
    next_cursor: str | None = None


class ChainOut(BaseModel):
    correlation_id: UUID
    depth: int
    model_calls: int
    actions: int
    cost_micros: int
    stopped_reason: str | None = None
    max_depth: int = 6
    max_model_calls: int = 30
    max_actions: int = 10
    cost_limit_micros: int = 500000
    steps: list[TeamActivityItem]
