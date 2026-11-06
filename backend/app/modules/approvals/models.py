"""Tables owned by approvals (07-workflow-engine.md#tables-owned-by-m08-approvals)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    correlation_id: Mapped[uuid.UUID | None]
    causation_id: Mapped[uuid.UUID | None]
    agent_key: Mapped[str | None] = mapped_column(String(32))
    chain_depth: Mapped[int | None]
    __table_args__ = scoped_table_args(
        CheckConstraint(f"status in {STATUSES}", name="status"),
        CheckConstraint("origin_kind in ('staff','assistant','rule')", name="origin_kind"),
        Index("ix_action_proposals_queue", "business_id", "status", "created_at"),
        Index("ix_action_proposals_chain", "business_id", "correlation_id"),
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
    decided_by: Mapped[uuid.UUID | None]
    decided_via: Mapped[str] = mapped_column(String(16), default="approvals", server_default="approvals")
    decided_by_kind: Mapped[str] = mapped_column(String(10), default="user", server_default="user")
    rule_id: Mapped[uuid.UUID | None]
    rule_version: Mapped[int | None]
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
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
        CheckConstraint(
            "decided_via in ('approvals','chat') and "
            "(decided_via != 'chat' or (decided_by_kind='user' and decision='approve'))",
            name="via",
        ),
        ForeignKeyConstraint(["business_id", "rule_id"], ["autonomy_rules.business_id", "autonomy_rules.id"]),
        CheckConstraint(
            "(decided_by_kind='user' and decided_by is not null and rule_id is null "
            "and rule_version is null and granted_by is null) or "
            "(decided_by_kind='policy' and decided_by is null and rule_id is not null "
            "and rule_version is not null and rule_version > 0 and granted_by is not null and decision='approve')",
            name="delegation",
        ),
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


class AutonomyRule(BusinessScopedBase):
    __tablename__ = "autonomy_rules"
    action_type: Mapped[str] = mapped_column(String(100))
    level: Mapped[str] = mapped_column(String(16), default="ask", server_default="ask")
    shadow: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    daily_cap: Mapped[int] = mapped_column(Integer, default=10, server_default=text("10"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    granted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "action_type"),
        CheckConstraint("level in ('off','draft','ask','act_notify','act')", name="level"),
        CheckConstraint("daily_cap >= 0 and version > 0", name="limits"),
    )


class AutonomyEvaluation(BusinessScopedBase):
    __tablename__ = "autonomy_evaluations"
    proposal_id: Mapped[uuid.UUID]
    rule_id: Mapped[uuid.UUID]
    rule_version: Mapped[int]
    outcome: Mapped[str] = mapped_column(String(16))
    reasons: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "proposal_id"], ["action_proposals.business_id", "action_proposals.id"]),
        ForeignKeyConstraint(["business_id", "rule_id"], ["autonomy_rules.business_id", "autonomy_rules.id"]),
        UniqueConstraint("business_id", "proposal_id"),
        CheckConstraint("outcome in ('acted','would_act','fell_back','blocked')", name="outcome"),
        CheckConstraint("rule_version > 0", name="rule_version"),
        Index("ix_autonomy_evaluations_activity", "business_id", "created_at", "id"),
    )


class AutonomyCounter(BusinessScopedBase):
    __tablename__ = "autonomy_counters"
    rule_id: Mapped[uuid.UUID]
    day: Mapped[date]
    count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "rule_id"], ["autonomy_rules.business_id", "autonomy_rules.id"]),
        UniqueConstraint("business_id", "rule_id", "day"),
        CheckConstraint("count >= 0", name="count"),
    )


class ChatConfirmCounter(BusinessScopedBase):
    __tablename__ = "chat_confirm_counters"
    day: Mapped[date]
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "day"),
        CheckConstraint("total_minor >= 0 and total_minor <= 100000", name="total"),
    )


class ChatConfirmReservation(BusinessScopedBase):
    __tablename__ = "chat_confirm_reservations"
    day: Mapped[date]
    subject: Mapped[str] = mapped_column(String(100))
    total_minor: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = scoped_table_args(
        UniqueConstraint("business_id", "day", "subject"),
        CheckConstraint("total_minor >= 0", name="total"),
    )
