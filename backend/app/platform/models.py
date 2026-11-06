"""SQLAlchemy base classes and the platform-owned tables (05-data-model.md#platform-owned-tables-m00).

Platform never imports module models. Foreign keys to `businesses` are declared by table name.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.platform.ids import new_id

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BusinessScopedBase(TimestampMixin, Base):
    """id (UUIDv7), business_id, version, timestamps, and unique(business_id, id) for composite FKs.

    Subclasses that need more table args must use `scoped_table_args(...)`.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return (UniqueConstraint("business_id", "id"),)


def scoped_table_args(*extra: Any) -> tuple[Any, ...]:
    return (UniqueConstraint("business_id", "id"), *extra)


# ----------------------------------------------------------------------------------------- platform tables
class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    kind: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    priority: Mapped[int] = mapped_column(SmallInteger, default=100, server_default=text("100"))
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=8, server_default=text("8"))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_until: Mapped[datetime | None]
    dedupe_key: Mapped[str | None] = mapped_column(String(300), unique=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[uuid.UUID | None]
    causation_id: Mapped[uuid.UUID | None]
    agent_key: Mapped[str | None] = mapped_column(String(32))
    chain_depth: Mapped[int | None]
    __table_args__ = (
        CheckConstraint("status in ('queued','running','succeeded','failed','dead')", name="status"),
        Index("ix_jobs_ready", "priority", "run_at", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_chain", "business_id", "correlation_id"),
    )


class DomainEvent(Base):
    __tablename__ = "domain_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"))
    type: Mapped[str] = mapped_column(String(100))
    schema_version: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    payload: Mapped[dict[str, Any]]
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    correlation_id: Mapped[uuid.UUID | None]
    causation_id: Mapped[uuid.UUID | None]
    agent_key: Mapped[str | None] = mapped_column(String(32))
    chain_depth: Mapped[int | None]
    __table_args__ = (Index("ix_domain_events_chain", "business_id", "correlation_id"),)


class EventDelivery(Base):
    __tablename__ = "event_deliveries"
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domain_events.id"), primary_key=True)
    subscriber: Mapped[str] = mapped_column(String(150), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_until: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    done_at: Mapped[datetime | None]
    __table_args__ = (
        CheckConstraint("status in ('pending','running','done','dead')", name="status"),
        Index("ix_event_deliveries_ready", "run_at", postgresql_where=text("status = 'pending'")),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"))
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None]
    on_behalf_of_user_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(120))
    target_type: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[uuid.UUID | None]
    proposal_id: Mapped[uuid.UUID | None]
    ai_run_id: Mapped[uuid.UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(String(500))
    changes: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    __table_args__ = (
        CheckConstraint("actor_kind in ('user','assistant','system','visitor')", name="actor_kind"),
        Index("ix_audit_events_business_time", "business_id", "occurred_at"),
        Index("ix_audit_events_target", "business_id", "target_type", "target_id"),
        Index(
            "ix_audit_events_views",
            "business_id",
            "actor_id",
            "action",
            "target_type",
            "target_id",
            text("occurred_at desc"),
            postgresql_where=text(
                "action in ('crm.customer.viewed','conversations.conversation.viewed','scheduling.appointment.viewed')"
            ),
        ),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[uuid.UUID | None]
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RateLimit(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger)


class JobDedupe(Base):
    """Compact permanent receipt: retention must never replay an already accepted action."""

    __tablename__ = "job_dedupe"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column()
