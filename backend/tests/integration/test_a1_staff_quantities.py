"""Staff order quantities are explicit, auditable and never delegated outside inventory's suggestion."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_copilot import send
from app.modules.approvals import engine
from app.modules.approvals.autonomy_schemas import RulePut
from app.modules.approvals.autonomy_service import put_rule
from app.modules.approvals.models import (
    ActionProposal,
    ApprovalDecision,
    AutonomyCounter,
    AutonomyEvaluation,
    ProposalRevision,
)
from app.modules.inventory import service as inventory
from app.modules.inventory.models import ReorderDraftLine
from app.modules.inventory.schemas import DraftLineIn, ReorderDraftIn
from app.modules.purchasing.models import PurchaseOrderLine, SupplierItem
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.registry import Registry
from tests.integration.test_a1_chat_confirm import BASE
from tests.integration.test_a1_chat_confirm import shop as shop


def turn(shop: Any, tool: str, message: str, **arguments: Any) -> tuple[Any, Any]:
    gateway = FakeGateway([step_tool(tool, **arguments), step_final("Please use the action card.", needs_human=False)])
    set_router_for_tests(fake_router_by_class(agent=gateway))
    try:
        assert send(shop["client"], shop["thread"], message)[-1]["kind"] == "final"
        detail = shop["client"].get(f"{BASE}/{shop['thread']}").json()
        return detail["messages"][-1]["proposals"][0], gateway
    finally:
        set_router_for_tests(None)


def suggestion(shop: Any, quantity: str = "5") -> None:
    response = shop["client"].put(
        f"/api/v1/inventory/reorder-rules/{shop['item']}", json={"reorder_point": "2", "target_level": quantity}
    )
    assert response.status_code == 200


def confirm(shop: Any, card: Any) -> Any:
    return shop["client"].post(
        f"{BASE}/{shop['thread']}/proposals/{card['id']}/confirm",
        json={"revision_id": card["revision_id"], "payload_hash": card["payload_hash"]},
    )


@pytest.mark.parametrize("provide_argument", [True, False])
def test_staff_ten_is_not_replaced_by_suggestion_five(shop: Any, provide_argument: bool) -> None:
    suggestion(shop)
    args = {"requested_quantities": [{"item_id": shop["item"], "quantity": "10"}]} if provide_argument else {}
    card, gateway = turn(
        shop,
        "propose_reorder",
        "Order 10 Example cable. Do not substitute a different quantity.",
        item_ids=[shop["item"]],
        **args,
    )
    assert card["can_confirm"]
    assert any("Quantity requested by Test User" in (p["after"] or "") for p in card["preview"])
    assert any("suggested 5" in (p["after"] or "").lower() for p in card["preview"])
    assert "Quantity requested by Test User" in gateway.calls[-1].messages[-1].content
    with transaction(shop["ctx"]) as db:
        revision = db.get(ProposalRevision, uuid.UUID(card["revision_id"]))
        assert revision.payload["lines"][0]["quantity"] == "10"
    assert confirm(shop, card).status_code == 200
    assert engine.execute(shop["business"], uuid.UUID(card["id"]), uuid.UUID(card["revision_id"])) == "succeeded"
    with transaction(shop["ctx"]) as db:
        line = db.scalars(select(ReorderDraftLine)).one()
        assert line.quantity == 10 and "Quantity requested by Test User" in line.reason_text


@pytest.mark.parametrize("provide_suggestion", [True, False])
def test_staff_without_quantity_keeps_suggestion(shop: Any, provide_suggestion: bool) -> None:
    suggestion(shop)
    args = {"requested_quantities": [{"item_id": shop["item"], "quantity": "5"}]} if provide_suggestion else {}
    card, gateway = turn(
        shop, "propose_reorder", "Reorder Example cable from the supplier.", item_ids=[shop["item"]], **args
    )
    with transaction(shop["ctx"]) as db:
        assert db.get(ProposalRevision, uuid.UUID(card["revision_id"])).payload["lines"][0]["quantity"] == "5"
    assert any("Suggested 5" in (p["after"] or "") for p in card["preview"])
    assert "Suggested 5" in gateway.calls[-1].messages[-1].content
    assert "Quantity requested by" not in gateway.calls[-1].messages[-1].content


def test_invented_quantity_observation_allows_retry_in_same_copilot_turn(shop: Any) -> None:
    from app.modules.ai.tests.test_intake_agent import observation

    suggestion(shop)
    calls = 0

    def model(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return step_tool(
                "propose_reorder",
                item_ids=[shop["item"]],
                requested_quantities=[{"item_id": shop["item"], "quantity": "10"}],
            )
        if calls == 2:
            error = observation(request)
            assert not error["ok"] and error["error"] == "ai.quantity_not_requested"
            assert "The user did not state a quantity" in error["detail"]
            assert "Call propose_reorder again without requested_quantities" in error["detail"]
            assert "suggested 5" in error["detail"]
            with transaction(shop["ctx"]) as db:
                assert list(db.scalars(select(ActionProposal))) == []
            return step_tool("propose_reorder", item_ids=[shop["item"]])
        result = observation(request)
        assert result["ok"] and result["data"]["confirmation"]["can_confirm"]
        assert result["data"]["quantities"][0]["requested_by"] is None
        return step_final("Proposed the suggested 5 cables; click Confirm.", needs_human=False)

    gateway = FakeGateway(model)
    set_router_for_tests(fake_router_by_class(agent=gateway))
    try:
        send(shop["client"], shop["thread"], "Reorder Example cable using the suggestion.")
        card = shop["client"].get(f"{BASE}/{shop['thread']}").json()["messages"][-1]["proposals"][0]
        assert calls == 3 and card["can_confirm"]
        with transaction(shop["ctx"]) as db:
            assert len(list(db.scalars(select(ActionProposal)))) == 1
            assert db.get(ProposalRevision, uuid.UUID(card["revision_id"])).payload["lines"][0]["quantity"] == "5"
            run = db.scalars(select(AiRun).where(AiRun.use_case == "copilot")).one()
            assert run.result["needs_human"] is False
    finally:
        set_router_for_tests(None)


@pytest.mark.parametrize("shadow", [True, False])
def test_autonomy_falls_back_for_staff_quantity_before_spending_cap(shop: Any, shadow: bool) -> None:
    suggestion(shop)
    ctx = shop["ctx"]
    action = "inventory.create_reorder_draft"
    with transaction(ctx) as db:
        put_rule(db, ctx, RulePut(action_type=action, level="act_notify", version=0))
    if not shadow:
        with transaction(ctx) as db:
            put_rule(db, ctx, RulePut(action_type=action, level="act_notify", shadow=False, version=1))
    card, _ = turn(
        shop,
        "propose_reorder",
        "Order 10 Example cable.",
        item_ids=[shop["item"]],
        requested_quantities=[{"item_id": shop["item"], "quantity": "10"}],
    )
    assert card["status"] == "pending_review" and card["can_confirm"]
    with transaction(ctx) as db:
        ev = db.scalars(select(AutonomyEvaluation)).one()
        assert ev.outcome == "fell_back" and ev.reasons == ["quantity_not_suggested"]
        assert list(db.scalars(select(ApprovalDecision))) == []
        assert list(db.scalars(select(AutonomyCounter))) == []
    assert confirm(shop, card).status_code == 200
    assert engine.execute(shop["business"], uuid.UUID(card["id"]), uuid.UUID(card["revision_id"])) == "succeeded"


def test_policy_executor_stays_strict_for_non_suggested_quantity(shop: Any, registry: Registry) -> None:
    suggestion(shop)
    ctx = dataclasses.replace(shop["ctx"], autonomy_rule_id=uuid.uuid4(), autonomy_rule_version=1)
    with pytest.raises(DomainError) as exc, transaction(ctx) as db:
        registry.executors["inventory.create_reorder_draft"].validate(
            db, ctx, ReorderDraftIn(lines=[DraftLineIn(item_id=shop["item"], quantity="10")])
        )
    assert exc.value.code == "inventory.reorder_changed"


@pytest.mark.parametrize("cost,status", [(1200, 200), (6000, 403)])
def test_requested_po_quantity_is_priced_and_capped(shop: Any, cost: int, status: int) -> None:
    suggestion(shop)
    ctx = shop["ctx"]
    with transaction(ctx) as db:
        db.scalars(select(SupplierItem)).one().last_price_minor = cost
        draft = inventory.create_draft(db, ctx, [DraftLineIn(item_id=shop["item"], quantity="5")], None)
    card, _gateway = turn(
        shop,
        "propose_send_po",
        "Order 10 Example cable from this draft.",
        draft_id=str(draft.id),
        supplier_id=shop["supplier"],
        requested_quantities=[{"item_id": shop["item"], "quantity": "10"}],
    )
    assert card["can_confirm"] == (status == 200)
    with transaction(ctx) as db:
        revision = db.get(ProposalRevision, uuid.UUID(card["revision_id"]))
        assert revision.payload["snapshot"]["lines"][0]["quantity"] == "10"
        assert revision.payload["snapshot"]["total_minor"] == 10 * cost
    assert "Quantity requested by Test User" in str(card["preview"])
    response = confirm(shop, card)
    assert response.status_code == status, response.text
    if status == 200:
        assert engine.execute(shop["business"], uuid.UUID(card["id"]), uuid.UUID(card["revision_id"])) == "succeeded"
        with transaction(ctx) as db:
            assert db.scalars(select(PurchaseOrderLine)).one().quantity == 10
    else:
        assert response.json()["code"] == "approvals.second_person_required"


@pytest.mark.parametrize("quantity", ["0", "-1", "10001", "0.0001"])
def test_staff_quantity_invalid_values_refused(shop: Any, quantity: str) -> None:
    gateway = FakeGateway(
        [
            step_tool(
                "propose_reorder",
                item_ids=[shop["item"]],
                requested_quantities=[{"item_id": shop["item"], "quantity": quantity}],
            ),
            step_final(needs_human=True),
        ]
    )
    set_router_for_tests(fake_router_by_class(agent=gateway))
    try:
        send(shop["client"], shop["thread"], f"Order {quantity} Example cable.")
        with transaction(shop["ctx"]) as db:
            assert list(db.scalars(select(ActionProposal))) == []
        assert "platform.validation_error" in gateway.calls[-1].messages[-1].content
    finally:
        set_router_for_tests(None)
