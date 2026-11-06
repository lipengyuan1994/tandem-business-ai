import uuid

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.models import BusinessScopedBase, scoped_table_args


class VoiceSession(BusinessScopedBase):
    __tablename__ = "voice_sessions"
    user_id: Mapped[uuid.UUID]
    context: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int | None]
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(100))
    operation_key: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = scoped_table_args(
        CheckConstraint("status in ('active','succeeded','failed')", name="status"),
        CheckConstraint("duration_ms is null or duration_ms >= 0", name="duration"),
        Index(
            "uq_voice_operation",
            "business_id",
            "user_id",
            "operation_key",
            unique=True,
            postgresql_where=text("operation_key is not null"),
        ),
    )
