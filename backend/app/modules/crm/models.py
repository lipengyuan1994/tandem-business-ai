from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


def _fk(col: str, table: str = "customers") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


class Customer(BusinessScopedBase):
    __tablename__ = "customers"
    kind: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(200))
    given_name: Mapped[str | None] = mapped_column(String(100))
    family_name: Mapped[str | None] = mapped_column(String(100))
    organization_name: Mapped[str | None] = mapped_column(String(200))
    preferred_contact_channel: Mapped[str] = mapped_column(String(10), default="email", server_default="email")
    preferred_language: Mapped[str | None] = mapped_column(String(20))
    timezone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10), default="active")
    merged_into_id: Mapped[uuid.UUID | None]
    last_interaction_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None]
    __table_args__ = scoped_table_args(
        CheckConstraint("preferred_contact_channel in ('email','web_chat','none')", name="contact_preference"),
        CheckConstraint("kind in ('person','organization')", name="kind"),
        CheckConstraint("status in ('active','archived','merged','erased','restricted')", name="status"),
        CheckConstraint("source in ('staff','public_chat','import','assistant')", name="source"),
    )


class ContactPoint(BusinessScopedBase):
    __tablename__ = "contact_points"
    customer_id: Mapped[uuid.UUID]
    type: Mapped[str] = mapped_column(String(8))
    value: Mapped[str] = mapped_column(String(320))
    value_normalized: Mapped[str] = mapped_column(String(320))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        _fk("customer_id"),
        CheckConstraint("type in ('email','phone')", name="type"),
        Index(
            "uq_contact_points_active",
            "business_id",
            "type",
            "value_normalized",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class ConsentRecord(BusinessScopedBase):
    __tablename__ = "consent_records"
    customer_id: Mapped[uuid.UUID]
    channel: Mapped[str] = mapped_column(String(8))
    purpose: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[str] = mapped_column(String(500))
    recorded_by: Mapped[uuid.UUID | None]
    recorded_at: Mapped[datetime]
    __table_args__ = scoped_table_args(
        _fk("customer_id"),
        CheckConstraint("channel in ('email','sms')", name="channel"),
        CheckConstraint("purpose in ('marketing','service_updates')", name="purpose"),
        CheckConstraint("status in ('granted','denied','withdrawn')", name="status"),
        CheckConstraint("source in ('form','staff','import','unsubscribe_link')", name="source"),
        Index("ix_consent_latest", "business_id", "customer_id", "channel", "purpose", "recorded_at"),
    )


class CustomerNote(BusinessScopedBase):
    __tablename__ = "customer_notes"
    customer_id: Mapped[uuid.UUID]
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None]
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = scoped_table_args(_fk("customer_id"))


class Tag(BusinessScopedBase):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(60))
    color: Mapped[str | None] = mapped_column(String(16))
    __table_args__ = scoped_table_args(Index("uq_tags_name", "business_id", "name", unique=True))


class CustomerTag(BusinessScopedBase):
    __tablename__ = "customer_tags"
    customer_id: Mapped[uuid.UUID]
    tag_id: Mapped[uuid.UUID]
    __table_args__ = scoped_table_args(
        _fk("customer_id"), _fk("tag_id", "tags"), Index("uq_customer_tags", "customer_id", "tag_id", unique=True)
    )


class TimelineEntry(BusinessScopedBase):
    __tablename__ = "timeline_entries"
    customer_id: Mapped[uuid.UUID]
    occurred_at: Mapped[datetime]
    kind: Mapped[str] = mapped_column(String(16))
    ref_module: Mapped[str] = mapped_column(String(30))
    ref_type: Mapped[str] = mapped_column(String(30))
    ref_id: Mapped[uuid.UUID]
    summary: Mapped[str] = mapped_column(String(200))
    __table_args__ = scoped_table_args(
        _fk("customer_id"),
        CheckConstraint(
            "kind in ('message','appointment','reservation','note','campaign','proposal','order','other')", name="kind"
        ),
        Index("uq_timeline_ref", "business_id", "ref_type", "ref_id", "kind", unique=True),
        Index("ix_timeline_customer", "business_id", "customer_id", "occurred_at"),
    )
