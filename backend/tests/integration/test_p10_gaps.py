"""P10 gap contracts on a real isolated database, synthetic rows and fake sessions only."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.modules.ai.agents import AGENTS
from app.modules.ai.customer_care.models import CareCase
from app.modules.ai.team_models import ChainBudget
from app.modules.ai.tests.helpers import insert_run
from app.modules.approvals.models import ActionProposal
from app.modules.conversations import public as conversations
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.db import transaction
from app.platform.models import DomainEvent
from app.platform.testing.auth import client_for
from tests.integration.customer_care_helpers import care as care
from tests.integration.customer_care_helpers import inbound
from tests.integration.test_a1_chat_confirm import order, proposal
from tests.integration.test_a1_chat_confirm import shop as shop


@pytest.mark.parametrize("size", [1, 2, 50])
def test_three_bands_keysets_ties_and_boundaries(care, registry, size):
    ctx, client = care["ctx"], care["client"]
    emergencies, cases, ordinary = [], [], []
    for _ in range(3):
        conv, _ = inbound(care)
        with transaction(ctx) as db:
            conversations.request_emergency_handoff(db, ctx, conv)
        emergencies.append(str(conv))
    for i, priority in enumerate(("normal", "urgent", "high", "urgent", "high", "normal") * (9 if size == 50 else 1)):
        conv, message = inbound(care)
        with transaction(ctx) as db:
            row = CareCase(
                business_id=ctx.business_id,
                customer_id=care["customer"],
                conversation_id=conv,
                source_message_id=message,
                category="warranty",
                priority=priority,
                status="open",
                created_at=clock.now() - timedelta(days=3, minutes=i // 6 % 2),
            )
            db.add(row)
            db.flush()
            cases.append((priority, row.created_at, str(row.id)))
    for _ in range(3):
        ordinary.append(str(proposal(care, order(care))["id"]))
    expected_cases = [
        ident
        for _, _, ident in sorted(
            cases, key=lambda x: ({"urgent": 2, "high": 1, "normal": 0}[x[0]], x[1], x[2]), reverse=True
        )
    ]
    # Frozen timestamps force the UUID tie-breaker in every band. Care is older than ordinary work.
    expected = sorted(emergencies, reverse=True) + expected_cases + sorted(ordinary, reverse=True)
    items, cursor, cursors = [], None, set()
    for _ in range(30):
        response = client.get("/api/v1/ai/work-queue", params={"limit": size, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["items"]) <= size
        items += result["items"]
        cursor = result["next_cursor"]
        if cursor is None:
            break
        assert cursor not in cursors
        cursors.add(cursor)
    else:
        pytest.fail("cursor did not terminate")
    assert [r["id"] for r in items] == expected
    assert len({r["id"] for r in items}) == len(expected)
    case = next(r for r in items if r["kind"] == "customer_care_case")
    assert (case["category"], case["version"], case["customer_name"]) == ("warranty", 1, "Ada Example")
    limited = client_for(care["app"], registry, ctx.business_id, ["approvals.proposal.read"])
    hidden = limited.get("/api/v1/ai/work-queue").json()["items"]
    assert all(r["customer_name"] is None for r in hidden)
    assert "ada@example.com" not in str(items) and "ada@example.com" not in str(hidden)
    foreign = client_for(
        care["app"],
        registry,
        make_business(slug="other-gaps-example"),
        ["approvals.proposal.read", "crm.customer.read"],
    )
    assert foreign.get("/api/v1/ai/work-queue").json()["items"] == []


@pytest.mark.parametrize("cursor", ["care:bad", "care:WzAsImJhZCJd", "care:", "emergency:", "ordinary:", "nonsense"])
def test_invalid_queue_cursor(care, cursor):
    assert care["client"].get("/api/v1/ai/work-queue", params={"cursor": cursor}).status_code == 422


@pytest.mark.parametrize("stopped", [False, True])
def test_measured_depth_from_runs_and_proposals_not_timeline_length(shop, stopped):
    root = uuid4()
    with transaction(shop["ctx"]) as db:
        db.add(
            DomainEvent(
                id=root,
                business_id=shop["business"],
                type="testing.root",
                payload={},
                actor_kind="system",
                correlation_id=root,
                chain_depth=0,
            )
        )
    root_only = shop["client"].get(f"/api/v1/ai/chains/{root}").json()
    assert root_only["depth"] == 0 and root_only["steps"][0]["depth"] == 0
    first = insert_run(shop["ctx"], agent_key="inventory", correlation_id=root, chain_depth=1, causation_id=root)
    second = insert_run(shop["ctx"], agent_key="purchasing", correlation_id=root, chain_depth=2, causation_id=first)
    p = proposal(shop, order(shop))
    with transaction(shop["ctx"]) as db:
        row = db.get(ActionProposal, p["id"])
        row.correlation_id, row.chain_depth, row.causation_id, row.agent_key = root, 3, second, "purchasing"
        db.add(
            ChainBudget(
                business_id=shop["business"],
                correlation_id=root,
                stopped_reason="ai.chain_call_limit" if stopped else None,
            )
        )
        # Events can retain different depth; only recorded runs/proposals determine measured chain usage.
        db.add(
            DomainEvent(
                business_id=shop["business"],
                type="testing.later",
                payload={},
                actor_kind="system",
                correlation_id=root,
                chain_depth=6,
            )
        )
    chain = shop["client"].get(f"/api/v1/ai/chains/{root}").json()
    assert chain["depth"] == 3
    assert chain["stopped_reason"] == ("ai.chain_call_limit" if stopped else None)
    depths = {r["id"]: r["depth"] for r in chain["steps"]}
    assert depths[str(first)] == 1 and depths[str(second)] == 2 and depths[str(p["id"])] == 3
    activity = shop["client"].get("/api/v1/ai/agents/purchasing/activity").json()["items"]
    assert next(r for r in activity if r["id"] == str(p["id"]))["depth"] == 3


def test_capabilities_match_every_registry_agent_and_enabled_patch(shop):
    rows = shop["client"].get("/api/v1/ai/agents").json()["items"]
    assert {r["key"] for r in rows} == set(AGENTS)
    for row in rows:
        assert row["triggers"] == list(AGENTS[row["key"]].triggers)
        assert "assistant_request" in row["triggers"]
        assert row["switchable"] is True
        response = shop["client"].patch(
            f"/api/v1/ai/agents/{row['key']}", json={"enabled": not row["enabled"], "version": row["version"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["switchable"] is True


def test_addressable_minimal_assistant_only_permissions_and_tenant_settings(shop, registry):
    assert (
        shop["client"].patch("/api/v1/ai/agents/operations_manager", json={"enabled": False, "version": 0}).status_code
        == 200
    )
    assistant = client_for(shop["app"], registry, shop["business"], ["ai.assistant.use"])
    result = assistant.get("/api/v1/ai/agents/addressable")
    assert result.status_code == 200, result.text
    data = result.json()
    assert set(data) == {"items", "manager_enabled", "manager_reason"}
    assert data["manager_enabled"] is False and data["manager_reason"] == "ai.agent_disabled"
    assert {r["key"] for r in data["items"]} == set(AGENTS)
    assert all(set(r) == {"key", "name", "enabled"} for r in data["items"])
    for path in ("agents", "agents/inventory/activity", f"chains/{uuid4()}", "work-queue"):
        assert assistant.get(f"/api/v1/ai/{path}").status_code == 403
    foreign = client_for(shop["app"], registry, make_business(slug="other-gaps-example"), ["ai.assistant.use"])
    other = foreign.get("/api/v1/ai/agents/addressable").json()
    assert other["manager_enabled"] is True and other["manager_reason"] is None
    assert next(r for r in other["items"] if r["key"] == "operations_manager")["enabled"] is True
    denied = client_for(shop["app"], registry, shop["business"], ["approvals.proposal.read"])
    assert denied.get("/api/v1/ai/agents/addressable").status_code == 403
    denied = client_for(shop["app"], registry, shop["business"], [])
    assert denied.get("/api/v1/ai/agents/addressable").status_code == 403
