from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.platform.registry import DeliveryState


class Evidence(BaseModel):
    kind: Literal["message", "tool_result", "record", "note"]
    ref_type: str | None = None
    ref_id: uuid.UUID | None = None
    excerpt: str = Field(default="", max_length=500)


class OpenProposal(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action_type: str
    title: str
    origin_kind: Literal["staff", "assistant", "rule"]
    created_at: datetime


class PreviewLineOut(BaseModel):
    label: str
    before: str | None
    after: str | None


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    revision_no: int
    payload: dict[str, Any]
    payload_hash: str
    summary: str
    evidence: list[Evidence]
    preview: list[PreviewLineOut]
    created_by_kind: str
    created_by: uuid.UUID | None
    created_at: datetime


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    revision_id: uuid.UUID
    decision: Literal["approve", "reject"]
    decided_by: uuid.UUID | None
    decided_by_kind: Literal["user", "policy"]
    decided_via: Literal["approvals", "chat"]
    rule_id: uuid.UUID | None
    rule_version: int | None
    granted_by: uuid.UUID | None
    decided_by_name: str = ""
    granted_by_name: str = ""
    roles_at_decision: list[str]
    comment: str | None
    decided_at: datetime


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    revision_id: uuid.UUID
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    result: dict[str, Any]
    error_code: str | None


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    action_type: str
    status: str
    title: str
    origin_kind: Literal["staff", "assistant", "rule"]
    origin_user_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    current_revision_id: uuid.UUID | None
    expires_at: datetime
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    version: int
    can_decide: bool = False


class ProposalDetailOut(ProposalOut):
    delivery_state: DeliveryState = "none"
    current_revision: RevisionOut
    revisions: list[RevisionOut]
    decisions: list[DecisionOut]
    attempts: list[AttemptOut]


class ProposalPage(BaseModel):
    items: list[ProposalOut]
    next_cursor: str | None


class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: str
    title: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any]
    evidence: list[Evidence] = Field(default_factory=list)


class RevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision_id: uuid.UUID
    payload: dict[str, Any]


class DecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    revision_id: uuid.UUID
    payload_hash: str = Field(min_length=64, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)


class ActionTypeOut(BaseModel):
    action_type: str
    approve_permission: str
    payload_schema: dict[str, Any]


class ChatConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: uuid.UUID
    payload_hash: str = Field(min_length=64, max_length=64)


class ChatCard(BaseModel):
    id: uuid.UUID
    title: str
    action_type: str
    preview: list[PreviewLineOut]
    status: str
    can_confirm: bool
    cannot_confirm_reason: str | None
    revision_id: uuid.UUID
    payload_hash: str
    delivery_state: DeliveryState
    result: dict[str, uuid.UUID] = Field(default_factory=dict)
