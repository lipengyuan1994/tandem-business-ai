from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


def _fk(col: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", col], [f"{table}.business_id", f"{table}.id"])


class Resource(BusinessScopedBase):
    __tablename__ = "resources"
    location_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(120))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    color: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(10), default="active")
    __table_args__ = scoped_table_args(
        _fk("location_id", "locations"),
        CheckConstraint("kind in ('staff','room','equipment')", name="kind"),
        CheckConstraint("status in ('active','archived')", name="status"),
        CheckConstraint("capacity = 1", name="capacity"),
    )


class ResourceService(BusinessScopedBase):
    __tablename__ = "resource_services"
    resource_id: Mapped[uuid.UUID]
    service_id: Mapped[uuid.UUID]
    __table_args__ = scoped_table_args(
        _fk("resource_id", "resources"),
        _fk("service_id", "catalog_items"),
        Index("uq_resource_services", "resource_id", "service_id", unique=True),
    )


class AvailabilityRule(BusinessScopedBase):
    __tablename__ = "availability_rules"
    resource_id: Mapped[uuid.UUID]
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = scoped_table_args(
        _fk("resource_id", "resources"),
        CheckConstraint("weekday between 0 and 6", name="weekday"),
        CheckConstraint("end_time > start_time", name="times"),
    )


class AvailabilityException(BusinessScopedBase):
    __tablename__ = "availability_exceptions"
    resource_id: Mapped[uuid.UUID | None]
    location_id: Mapped[uuid.UUID]
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    kind: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = scoped_table_args(
        _fk("resource_id", "resources"),
        _fk("location_id", "locations"),
        CheckConstraint("kind in ('closed','extra_open')", name="kind"),
        CheckConstraint("ends_at > starts_at", name="times"),
    )


class Appointment(BusinessScopedBase):
    __tablename__ = "appointments"
    customer_id: Mapped[uuid.UUID]
    service_id: Mapped[uuid.UUID]
    resource_id: Mapped[uuid.UUID]
    location_id: Mapped[uuid.UUID]
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    blocked_until: Mapped[datetime]
    timezone: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))
    hold_expires_at: Mapped[datetime | None]
    proposal_id: Mapped[uuid.UUID | None]
    source: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    cancelled_reason: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = scoped_table_args(
        _fk("customer_id", "customers"),
        _fk("service_id", "catalog_items"),
        _fk("resource_id", "resources"),
        _fk("location_id", "locations"),
        CheckConstraint("status in ('held','confirmed','cancelled','completed','no_show')", name="status"),
        CheckConstraint("source in ('staff','assistant','public_request')", name="source"),
        CheckConstraint("ends_at > starts_at and blocked_until >= ends_at", name="times"),
        CheckConstraint("status <> 'held' or hold_expires_at is not null", name="hold_expiry"),
        Index("ix_appointments_resource_time", "business_id", "resource_id", "starts_at"),
    )
