"""P10-C1 part 2: grounding accounting and withheld web-chat publication."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261105_1100_grounded_publication"
down_revision = "20261105_1000_knowledge_handoff"
branch_labels = None
depends_on = None

OLD_CASES = "'intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft','payment_followup','safety'"
OLD_CLASSES = "'agent','fast_extract','writer','safety','embed','stt','tts'"


def _checks(extra: str) -> None:
    for column, values in (("use_case", OLD_CASES), ("model_class", OLD_CLASSES)):
        op.drop_constraint(column, "ai_runs", type_="check")
        op.create_check_constraint(column, "ai_runs", f"{column} in ({values}{extra})")


def upgrade() -> None:
    _checks(", 'grounding'")
    op.add_column("messages", sa.Column("publication_state", sa.String(12), nullable=False, server_default="published"))
    for name in ("publish_at", "published_at"):
        op.add_column("messages", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("messages", sa.Column("answers_message_id", UUID(as_uuid=True)))
    op.add_column("messages", sa.Column("cancellation_reason", sa.String(40)))
    op.create_foreign_key("fk_messages_business_id_answers_message_id_messages", "messages", "messages",
                          ["business_id", "answers_message_id"], ["business_id", "id"])
    op.create_check_constraint("publication_state", "messages",
                               "publication_state in ('withheld','published','stopped','cancelled')")
    op.create_check_constraint("publication_due", "messages",
                               "publication_state != 'withheld' or (publish_at is not null and proposal_id is not null)")
    op.create_index("ix_messages_publication", "messages", ["business_id", "publish_at"],
                    postgresql_where=sa.text("publication_state='withheld'"))


def downgrade() -> None:
    op.execute("do $$ begin if exists (select 1 from messages where publication_state != 'published' or publish_at is not null) "
               "or exists (select 1 from ai_runs where use_case='grounding' or model_class='grounding') "
               "then raise exception 'C1 grounding/publication history must be retained'; end if; end $$")
    op.drop_index("ix_messages_publication", table_name="messages")
    op.drop_constraint("publication_due", "messages", type_="check")
    op.drop_constraint("publication_state", "messages", type_="check")
    op.drop_constraint("fk_messages_business_id_answers_message_id_messages", "messages", type_="foreignkey")
    for name in ("cancellation_reason", "answers_message_id", "published_at", "publish_at", "publication_state"):
        op.drop_column("messages", name)
    _checks("")
