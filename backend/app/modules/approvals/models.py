"""Tables owned by approvals (07-workflow-engine.md#tables-owned-by-m08-approvals)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args

STATUSES = ("pending_review", "approved", "rejected", "expired", "cancelled", "executing", "succeeded", "failed")
CLOSED = frozenset({"rejected", "expired", "cancelled", "succeeded", "failed"})


class ActionProposal(BusinessScopedBase):
    __tablename__ = "action_proposals"
    action_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending_review")
    current_revision_id: Mapped[uuid.UUID | None]
    origin_kind: Mapped[str] = mapped_column(String(16))
    origin_user_id: Mapped[uuid.UUID | None]
    ai_run_id: Mapped[uuid.UUID | None]
    conversation_id: Mapped[uuid.UUID | None]
    title: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime]
    failure_code: Mapped[str | None] = mapped_column(String(120))
    __table_args__ = scoped_table_args(
        CheckConstraint(f"status in {STATUSES}", name="status"),
        CheckConstraint("origin_kind in ('staff','assistant','rule')", name="origin_kind"),
        Index("ix_action_proposals_queue", "business_id", "status", "created_at"),
    )


class ProposalRevision(BusinessScopedBase):
    __tablename__ = "proposal_revisions"
    proposal_id: Mapped[uuid.UUID]
    revision_no: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(String(500))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    preview: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_by_kind: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "proposal_id"], ["action_proposals.business_id", "action_proposals.id"]),
        Index("uq_proposal_revisions_no", "proposal_id", "revision_no", unique=True),
    )


class ApprovalDecision(BusinessScopedBase):
    __tablename__ = "approval_decisions"
    proposal_id: Mapped[uuid.UUID]
    revision_id: Mapped[uuid.UUID]
    decision: Mapped[str] = mapped_column(String(10))
    decided_by: Mapped[uuid.UUID]
    roles_at_decision: Mapped[list[str]] = mapped_column(ARRAY(String(40)))
    policy_version: Mapped[str] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "proposal_id"], ["action_proposals.business_id", "action_proposals.id"]),
        ForeignKeyConstraint(
            ["business_id", "revision_id"], ["proposal_revisions.business_id", "proposal_revisions.id"]
        ),
        CheckConstraint("decision in ('approve','reject')", name="decision"),
    )


class ExecutionAttempt(BusinessScopedBase):
    __tablename__ = "execution_attempts"
    proposal_id: Mapped[uuid.UUID]
    revision_id: Mapped[uuid.UUID]
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    acting_user_id: Mapped[uuid.UUID | None]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "proposal_id"], ["action_proposals.business_id", "action_proposals.id"]),
        CheckConstraint("status in ('running','succeeded','failed')", name="status"),
    )
