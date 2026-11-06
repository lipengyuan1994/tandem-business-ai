"""P10-D metadata-only dictation sessions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from migrations.helpers import scoped_columns, scoped_table

revision = "20261106_0800_voice_dictation"
down_revision = "20261105_1100_grounded_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("use_case", "ai_runs", type_="check")
    op.create_check_constraint("use_case", "ai_runs",
        "use_case in ('intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft',"
        "'payment_followup','safety','grounding','dictation')")
    op.create_table("voice_sessions", *scoped_columns(),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("context", sa.String(20), nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("provider", sa.String(100)),
        sa.Column("model", sa.String(200)),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("operation_key", sa.String(64)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("business_id", "id"),
        sa.CheckConstraint("status in ('active','succeeded','failed')", name="status"),
        sa.CheckConstraint("duration_ms is null or duration_ms >= 0", name="duration"))
    op.create_index("uq_voice_operation", "voice_sessions", ["business_id", "user_id", "operation_key"],
                    unique=True, postgresql_where=sa.text("operation_key is not null"))
    op.execute("grant select, insert, update, delete on voice_sessions to smb_app")
    scoped_table("voice_sessions")


def downgrade() -> None:
    op.drop_table("voice_sessions")
    op.execute("delete from ai_run_steps where run_id in (select id from ai_runs where use_case='dictation')")
    op.execute("delete from ai_runs where use_case='dictation'")
    op.drop_constraint("use_case", "ai_runs", type_="check")
    op.create_check_constraint("use_case", "ai_runs",
        "use_case in ('intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft',"
        "'payment_followup','safety','grounding')")
