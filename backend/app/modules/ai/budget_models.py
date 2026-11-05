"""Budget-only counters. Global rows contain aggregate cost, never tenant data or model text."""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import Base, BusinessScopedBase, scoped_table_args


class CostPeriod(Base):
    __tablename__ = "ai_cost_periods"
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0)


class PublicAllowance(BusinessScopedBase):
    __tablename__ = "ai_public_allowances"
    period: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(8))
    used: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = scoped_table_args(Index("uq_ai_public_allowance", "business_id", "kind", "period", unique=True))


class BudgetNotice(BusinessScopedBase):
    __tablename__ = "ai_budget_notices"
    scope: Mapped[str] = mapped_column(String(24))
    period: Mapped[date] = mapped_column(Date)
    threshold: Mapped[int]
    __table_args__ = scoped_table_args(
        Index("uq_ai_budget_notice", "business_id", "scope", "period", "threshold", unique=True)
    )
