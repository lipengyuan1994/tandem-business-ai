"""Upgrade compatibility: existing operational rows keep null causality and default behavior."""

from uuid import uuid4

from alembic import command
from sqlalchemy import select, text

from app.modules.ai.agents import AGENTS
from app.modules.ai.models import AiRun
from app.modules.ai.team_settings import enabled
from app.modules.approvals.models import ActionProposal
from app.platform.context import system_context
from app.platform.db import transaction
from app.platform.models import DomainEvent, Job
from tests.architecture.test_migrations import _cfg


def test_upgrade_keeps_existing_rows_null_and_agents_enabled(owner_engine, business):
    cfg = _cfg()
    try:
        command.downgrade(cfg, "20261106_0800_voice_dictation")
        with owner_engine.begin() as db:
            db.execute(
                text(
                    "insert into ai_runs(id,business_id,use_case,trigger_type,trigger_key,prompt_name,"
                    "prompt_version,model_class,status) values (:id,:b,'reorder_note','job','legacy',"
                    "'reorder_note',1,'writer','succeeded')"
                ),
                {"id": uuid4(), "b": business},
            )
            db.execute(
                text(
                    "insert into action_proposals(id,business_id,action_type,status,origin_kind,title,expires_at) "
                    "values (:id,:b,'inventory.create_reorder_draft','pending_review','assistant',"
                    "'Example legacy reorder',now()+interval '1 day')"
                ),
                {"id": uuid4(), "b": business},
            )
            db.execute(
                text(
                    "insert into domain_events(id,business_id,type,schema_version,payload,actor_kind) "
                    "values (:id,:b,'inventory.stock_below_reorder_level',1,'{}','system')"
                ),
                {"id": uuid4(), "b": business},
            )
            db.execute(
                text("insert into jobs(id,business_id,kind,payload) values (:id,:b,'ai.reorder_note','{}')"),
                {"id": uuid4(), "b": business},
            )
        command.upgrade(cfg, "head")
        with owner_engine.connect() as db:
            for model in (AiRun, ActionProposal, DomainEvent, Job):
                assert db.execute(
                    select(model.correlation_id, model.causation_id, model.agent_key, model.chain_depth)
                ).one() == (None, None, None, None)
        ctx = system_context(business)
        with transaction(ctx) as db:
            assert all(enabled(db, ctx, key) == (key != "customer_care") for key in AGENTS)
    finally:
        command.upgrade(cfg, "head")
