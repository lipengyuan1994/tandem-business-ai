from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


def _fk(col: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


class CareCase(BusinessScopedBase):
    __tablename__ = "customer_care_cases"
    customer_id: Mapped[uuid.UUID | None]
    conversation_id: Mapped[uuid.UUID]
    source_message_id: Mapped[uuid.UUID]
    category: Mapped[str] = mapped_column(String(16))
    priority: Mapped[str] = mapped_column(String(10), default="high")
    status: Mapped[str] = mapped_column(String(10), default="open")
    appointment_id: Mapped[uuid.UUID | None]
    invoice_id: Mapped[uuid.UUID | None]
    proposal_id: Mapped[uuid.UUID | None]
    resolved_by: Mapped[uuid.UUID | None]
    resolved_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        _fk("customer_id", "customers"),
        _fk("conversation_id", "conversations"),
        _fk("source_message_id", "messages"),
        _fk("appointment_id", "appointments"),
        _fk("invoice_id", "invoices"),
        _fk("proposal_id", "action_proposals"),
        Index("uq_care_case_source", "business_id", "source_message_id", unique=True),
        Index("ix_care_case_customer", "business_id", "customer_id", "status"),
        CheckConstraint("category in ('complaint','warranty','dispute')", name="category"),
        CheckConstraint("priority in ('normal','high','urgent')", name="priority"),
        CheckConstraint("status in ('open','resolved')", name="status"),
    )


class CareDelivery(BusinessScopedBase):
    """Committed executor receipt. Identifiers prevent re-sending even after Stop or erasure."""

    __tablename__ = "customer_care_deliveries"
    customer_id: Mapped[uuid.UUID]
    domain_key: Mapped[str] = mapped_column(String(240))
    proposal_id: Mapped[uuid.UUID]
    outbox_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    # Only metadata needed to recheck delivery. Message text stays in existing lifecycle-owned rows.
    kind: Mapped[str] = mapped_column(String(20))
    source_message_id: Mapped[uuid.UUID | None]
    appointment_id: Mapped[uuid.UUID | None]
    service_id: Mapped[uuid.UUID | None]
    review_requested: Mapped[bool]
    due_month: Mapped[str | None] = mapped_column(String(7))
    ai_generation: Mapped[int | None]
    not_before: Mapped[datetime]
    __table_args__ = scoped_table_args(
        _fk("customer_id", "customers"),
        _fk("proposal_id", "action_proposals"),
        _fk("message_id", "messages"),
        _fk("source_message_id", "messages"),
        _fk("appointment_id", "appointments"),
        _fk("service_id", "catalog_items"),
        Index("uq_care_delivery_subject", "business_id", "domain_key", unique=True),
        Index("ix_care_delivery_outbox", "business_id", "outbox_id"),
        Index("uq_care_delivery_proposal", "business_id", "proposal_id", unique=True),
    )
