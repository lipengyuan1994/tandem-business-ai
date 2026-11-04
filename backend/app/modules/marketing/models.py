from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


def _fk(col: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


class Segment(BusinessScopedBase):
    __tablename__ = "segments"
    name: Mapped[str] = mapped_column(String(120))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Campaign(BusinessScopedBase):
    __tablename__ = "campaigns"
    name: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(8), default="email")
    segment_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(12), default="draft")
    current_revision_id: Mapped[uuid.UUID | None]
    submitted_snapshot_id: Mapped[uuid.UUID | None]
    approved_snapshot_id: Mapped[uuid.UUID | None]
    proposal_id: Mapped[uuid.UUID | None]
    sent_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        _fk("segment_id", "segments"),
        CheckConstraint("channel in ('email')", name="channel"),
        CheckConstraint("status in ('draft','in_review','approved','sending','sent','cancelled')", name="status"),
    )


class CampaignRevision(BusinessScopedBase):
    """Immutable: editing a campaign adds a revision."""

    __tablename__ = "campaign_revisions"
    campaign_id: Mapped[uuid.UUID]
    revision_no: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(200))
    body_markdown: Mapped[str] = mapped_column(Text)
    created_by_kind: Mapped[str] = mapped_column(String(10))
    created_by: Mapped[uuid.UUID | None]
    ai_run_id: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        _fk("campaign_id", "campaigns"),
        Index("uq_campaign_revisions_no", "campaign_id", "revision_no", unique=True),
        CheckConstraint("created_by_kind in ('staff','assistant')", name="created_by_kind"),
    )


class AudienceSnapshot(BusinessScopedBase):
    __tablename__ = "audience_snapshots"
    campaign_id: Mapped[uuid.UUID]
    segment_definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    computed_at: Mapped[datetime]
    eligible_count: Mapped[int] = mapped_column(Integer)
    excluded_counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = scoped_table_args(_fk("campaign_id", "campaigns"))


class AudienceMember(BusinessScopedBase):
    __tablename__ = "audience_members"
    snapshot_id: Mapped[uuid.UUID]
    customer_id: Mapped[uuid.UUID]
    contact_point_id: Mapped[uuid.UUID]
    __table_args__ = scoped_table_args(
        _fk("snapshot_id", "audience_snapshots"),
        _fk("customer_id", "customers"),
        _fk("contact_point_id", "contact_points"),
        Index("uq_audience_members", "snapshot_id", "customer_id", unique=True),
    )


class Suppression(BusinessScopedBase):
    """Addresses that must never receive marketing again. Append-only; the value is personal data."""

    __tablename__ = "suppressions"
    channel: Mapped[str] = mapped_column(String(8))
    value_normalized: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(String(12))
    __table_args__ = scoped_table_args(
        Index("uq_suppressions", "business_id", "channel", "value_normalized", unique=True),
        CheckConstraint("channel in ('email')", name="channel"),
        CheckConstraint("reason in ('unsubscribed','bounced','complaint','manual','erased')", name="reason"),
    )
