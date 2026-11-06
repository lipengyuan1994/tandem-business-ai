"""Tenant-isolated configuration and budget accounting, never another work queue."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


class AgentSettings(BusinessScopedBase):
    __tablename__ = "ai_agent_settings"
    agent_key: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(default=True)
    daily_cost_limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = scoped_table_args(
        Index("uq_ai_agent_settings_key", "business_id", "agent_key", unique=True),
        CheckConstraint("daily_cost_limit_usd is null or daily_cost_limit_usd >= 0", name="limit"),
    )


class AgentUsageDaily(BusinessScopedBase):
    __tablename__ = "ai_agent_usage_daily"
    agent_key: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date)
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    notified_at: Mapped[datetime | None]
    __table_args__ = scoped_table_args(Index("uq_ai_agent_usage_day", "business_id", "agent_key", "day", unique=True))


class ChainBudget(BusinessScopedBase):
    __tablename__ = "ai_chain_budgets"
    correlation_id: Mapped[UUID]
    model_calls: Mapped[int] = mapped_column(default=0, server_default="0")
    actions: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    stopped_reason: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = scoped_table_args(Index("uq_ai_chain_budget", "business_id", "correlation_id", unique=True))
