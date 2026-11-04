from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FieldName = Literal["tag", "last_interaction_at", "created_at", "has_appointment_for_service", "customer_kind"]
Op = Literal["has", "older_than_days", "newer_than_days", "eq"]
ALLOWED_OPS: dict[str, set[str]] = {
    "tag": {"has"},
    "last_interaction_at": {"older_than_days", "newer_than_days"},
    "created_at": {"older_than_days", "newer_than_days"},
    "has_appointment_for_service": {"eq"},
    "customer_kind": {"eq"},
}


class Clause(BaseModel):
    """One whitelisted filter. There is no free-form SQL anywhere in a segment."""

    model_config = ConfigDict(extra="forbid")
    field: FieldName
    op: Op
    value: str | int

    @model_validator(mode="after")
    def _check(self) -> Clause:
        if self.op not in ALLOWED_OPS[self.field]:
            raise ValueError(f"operator {self.op} is not allowed for {self.field}")
        if self.field == "tag":
            if not isinstance(self.value, str) or not 1 <= len(self.value.strip()) <= 60:
                raise ValueError("tag must be text of 1-60 characters")
        elif self.field in ("last_interaction_at", "created_at"):
            if isinstance(self.value, bool) or not isinstance(self.value, int) or not 1 <= self.value <= 3650:
                raise ValueError("days must be a whole number from 1 to 3650")
        elif self.field == "has_appointment_for_service":
            try:
                uuid.UUID(str(self.value))
            except ValueError as exc:
                raise ValueError("service id must be a UUID") from exc
        elif self.value not in ("person", "organization"):
            raise ValueError("customer_kind must be person or organization")
        return self


class SegmentDefinition(BaseModel):
    """All clauses must match (AND). At least one is required, so nothing targets 'everyone' by accident."""

    model_config = ConfigDict(extra="forbid")
    all: Annotated[list[Clause], Field(min_length=1, max_length=10)]


class SegmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    definition: SegmentDefinition


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    definition: dict[str, Any]
    version: int
    created_at: datetime


class AudienceSample(BaseModel):
    customer_id: uuid.UUID
    display_name: str


class AudiencePreview(BaseModel):
    matched: int
    eligible_count: int
    excluded_counts: dict[str, int]
    sample: list[AudienceSample]
    offset: int
    members: list[AudienceSample]
    hash: str


class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    purpose: str | None = Field(default=None, max_length=1000)
    segment_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=20000)


class CampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=160)
    purpose: str | None = Field(default=None, max_length=1000)
    segment_id: uuid.UUID | None = None


class CampaignRevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=20000)


class CampaignRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    revision_no: int
    subject: str
    body_markdown: str
    created_by_kind: str
    created_at: datetime


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    computed_at: datetime
    eligible_count: int
    excluded_counts: dict[str, int]
    hash: str


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    purpose: str | None
    channel: str
    segment_id: uuid.UUID
    status: str
    current_revision: CampaignRevisionOut | None
    snapshot: SnapshotOut | None
    proposal_id: uuid.UUID | None
    sent_at: datetime | None
    version: int
    created_at: datetime


class CampaignDetail(CampaignOut):
    revisions: list[CampaignRevisionOut]


class SuppressionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=3, max_length=320)
    reason: Literal["manual", "bounced", "complaint"] = "manual"


class SuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    channel: str
    value_normalized: str
    reason: str
    created_at: datetime


class ApproveCampaignPayload(BaseModel):
    """marketing.approve_campaign proposal payload."""

    model_config = ConfigDict(extra="forbid")
    campaign_id: uuid.UUID
    revision_id: uuid.UUID
    snapshot_hash: str = Field(min_length=64, max_length=64)


class AudiencePreviewParams(BaseModel):
    offset: int = 0
    limit: int = 10


__all__ = [
    "ApproveCampaignPayload",
    "AudiencePreview",
    "AudienceSample",
    "CampaignCreate",
    "CampaignDetail",
    "CampaignOut",
    "CampaignRevisionIn",
    "CampaignRevisionOut",
    "CampaignUpdate",
    "Clause",
    "SegmentDefinition",
    "SegmentIn",
    "SegmentOut",
    "SnapshotOut",
    "SuppressionIn",
    "SuppressionOut",
]
