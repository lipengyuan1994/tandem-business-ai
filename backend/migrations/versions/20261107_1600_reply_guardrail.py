"""P10-G: record reply guardrail checks as an independent use case."""

from alembic import op

revision = "20261107_1600_reply_guardrail"
down_revision = "20261106_1500_self_service"
branch_labels = None
depends_on = None

CASES = (
    "'intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft',"
    "'payment_followup','safety','grounding','dictation','customer_care','knowledge_index','knowledge_query'"
)


def upgrade() -> None:
    op.drop_constraint("use_case", "ai_runs", type_="check")
    op.create_check_constraint("use_case", "ai_runs", f"use_case in ({CASES},'reply_guardrail')")


def downgrade() -> None:
    # Never silently delete audit/usage history to make a downgrade possible.
    op.execute(
        "do $$ begin if exists (select 1 from ai_runs where use_case='reply_guardrail') "
        "then raise exception 'Reply guardrail history must be retained'; end if; end $$"
    )
    op.drop_constraint("use_case", "ai_runs", type_="check")
    op.create_check_constraint("use_case", "ai_runs", f"use_case in ({CASES})")
