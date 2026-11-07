"""Hash-only customer capabilities and immutable-in-use change receipts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


def customer_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["business_id", "customer_id"], ["customers.business_id", "customers.id"])


class SelfServiceToken(BusinessScopedBase):
    __tablename__ = "self_service_tokens"
    customer_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(customer_fk(), Index("uq_self_service_token", "token_hash", unique=True))


class SelfServiceSession(BusinessScopedBase):
    __tablename__ = "self_service_sessions"
    customer_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(customer_fk(), Index("uq_self_service_session", "token_hash", unique=True))


class SelfServiceChange(BusinessScopedBase):
    __tablename__ = "self_service_changes"
    customer_id: Mapped[uuid.UUID]
    appointment_id: Mapped[uuid.UUID]
    action: Mapped[str] = mapped_column(String(16))
    fee_acknowledged: Mapped[bool]
    fee_text: Mapped[str | None] = mapped_column(String(1000))
    acknowledged_at: Mapped[datetime | None]
    appointment_version: Mapped[int]
    __table_args__ = scoped_table_args(
        customer_fk(),
        ForeignKeyConstraint(["business_id", "appointment_id"], ["appointments.business_id", "appointments.id"]),
    )
