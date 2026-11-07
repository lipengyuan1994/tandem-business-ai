"""Tables owned by the org module (docs/roadmap/modules/M02-org.md)."""

from __future__ import annotations

import uuid
from datetime import time
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, Index, Numeric, SmallInteger, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.ids import new_id
from app.platform.models import Base, BusinessScopedBase, TimestampMixin, scoped_table_args


class Business(TimestampMixin, Base):
    __tablename__ = "businesses"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    synthetic_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    locale: Mapped[str] = mapped_column(String(16), default="en-US", server_default="en-US")
    country: Mapped[str | None] = mapped_column(String(2))
    public_profile: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9-]{3,40}$'", name="slug"),
        CheckConstraint("status in ('active','suspended')", name="status"),
    )


class Location(BusinessScopedBase):
    __tablename__ = "locations"
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    timezone: Mapped[str] = mapped_column(String(64))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    __table_args__ = scoped_table_args(
        Index("uq_locations_one_default", "business_id", unique=True, postgresql_where=text("is_default")),
    )


class OpeningHours(BusinessScopedBase):
    __tablename__ = "opening_hours"
    location_id: Mapped[uuid.UUID]
    weekday: Mapped[int] = mapped_column(SmallInteger)
    opens: Mapped[time] = mapped_column(Time)
    closes: Mapped[time] = mapped_column(Time)
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(
            ["business_id", "location_id"], ["locations.business_id", "locations.id"], ondelete="CASCADE"
        ),
        CheckConstraint("weekday between 0 and 6", name="weekday"),
        CheckConstraint("closes > opens", name="closes_after_opens"),
    )


class BusinessSettingsRow(BusinessScopedBase):
    __tablename__ = "business_settings"
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    sales_tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"), server_default=text("0"))
    __table_args__ = scoped_table_args(
        Index("uq_business_settings_business", "business_id", unique=True),
        CheckConstraint("sales_tax_rate between 0 and 1", name="sales_tax_rate"),
    )
