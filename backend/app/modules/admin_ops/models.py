from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


class ErasureRequest(BusinessScopedBase):
    """Who asked to erase which customer and what was done. Holds ids and counts only, never personal data."""

    __tablename__ = "erasure_requests"
    customer_id: Mapped[uuid.UUID]
    requested_by: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(String(12))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    completed_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "customer_id"], ["customers.business_id", "customers.id"]),
        CheckConstraint("status in ('completed','blocked','restricted')", name="status"),
    )
