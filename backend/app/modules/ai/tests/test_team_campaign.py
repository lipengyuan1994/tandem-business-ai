"""Campaign revision denial must leave chain and run exception evidence."""

from sqlalchemy import select

from app.modules.ai import team_budget
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.team_models import ChainBudget
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.ai.tests.test_campaign_draft import GOOD, campaign
from app.modules.ai.tests.test_campaign_draft import clean_router as clean_router
from app.platform.context import system_context
from app.platform.db import transaction


def test_denied_campaign_action_records_terminal_exception(registry, monkeypatch):
    import json

    business, client, campaign_id = campaign(registry)
    original_revision = client.get(f"/api/v1/marketing/campaigns/{campaign_id}").json()["current_revision"]["id"]
    set_router_for_tests(fake_router_by_class(writer=FakeGateway([json.dumps(GOOD)])))

    def deny(db, ctx):
        raise AiError("ai.chain_action_limit")

    monkeypatch.setattr(team_budget, "reserve_action", deny)
    response = client.post(f"/api/v1/ai/campaigns/{campaign_id}/draft")
    assert response.status_code == 429 and response.json()["code"] == "ai.chain_action_limit"
    with transaction(system_context(business)) as db:
        run = db.scalars(select(AiRun)).one()
        assert run.status == "policy_blocked" and run.error_code == "ai.chain_action_limit"
        assert db.scalar(select(ChainBudget.stopped_reason)) == "ai.chain_action_limit"
    detail = client.get(f"/api/v1/marketing/campaigns/{campaign_id}").json()
    assert detail["current_revision"]["id"] == original_revision and len(detail["revisions"]) == 1
