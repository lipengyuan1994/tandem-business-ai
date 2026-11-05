"""Staff quantity provenance, permissions, legacy hashes and actual-money daily reservations."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import agent_step_json_schema
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.ai.models import AiRun, AssistantMessage
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_copilot import send
from app.modules.ai.tests.tool_helpers import assistant
from app.modules.ai.tools.registry import authorized_tools, execute_tool
from app.modules.approvals import engine
from app.modules.approvals import service as approvals
from app.modules.approvals.autonomy_schemas import RulePut
from app.modules.approvals.autonomy_service import put_rule
from app.modules.approvals.models import (
    ActionProposal,
    ApprovalDecision,
    AutonomyEvaluation,
    ChatConfirmCounter,
    ProposalRevision,
)
from app.modules.inventory import service as inventory
from app.modules.inventory.schemas import DraftLineIn
from app.modules.purchasing.models import SupplierItem
from app.modules.purchasing.schemas import CreateFromDraftPayload
from app.platform.context import assistant_context
from app.platform.db import transaction
from tests.integration.test_a1_chat_confirm import BASE
from tests.integration.test_a1_chat_confirm import shop as shop
from tests.integration.test_a1_staff_quantities import confirm, suggestion, turn


def refused(shop: Any, message: str, **arguments: Any) -> str:
    gateway = FakeGateway([step_tool("propose_reorder", **arguments), step_final(needs_human=True)])
    set_router_for_tests(fake_router_by_class(agent=gateway))
    try:
        send(shop["client"], shop["thread"], message)
        with transaction(shop["ctx"]) as db:
            assert list(db.scalars(select(ActionProposal))) == []
        return gateway.calls[-1].messages[-1].content
    finally:
        set_router_for_tests(None)


def test_quantity_for_another_item_is_never_attributed_to_selected_item(shop: Any) -> None:
    suggestion(shop)
    other = (
        shop["client"]
        .post("/api/v1/catalog/items", json={"name": "Other cable", "kind": "product", "is_stock_tracked": True})
        .json()["id"]
    )
    detail = refused(shop, "Order 10 Example cable.", item_ids=[other])
    assert "ai.quantity_item_mismatch" in detail


@pytest.mark.parametrize(
    "wording", ["Order Example cable ×10.", "Order Example cable x 10.", "Order Example cable quantity 10."]
)
def test_item_then_quantity_preserved_when_model_omits_argument(shop: Any, wording: str) -> None:
    suggestion(shop)
    card, _ = turn(shop, "propose_reorder", wording, item_ids=[shop["item"]])
    with transaction(shop["ctx"]) as db:
        assert db.get(ProposalRevision, uuid.UUID(card["revision_id"])).payload["lines"][0]["quantity"] == "10"


def test_stock_fact_is_not_mistaken_for_order_quantity(shop: Any) -> None:
    suggestion(shop)
    card, _ = turn(
        shop,
        "propose_reorder",
        "We have 10 Example cable. Reorder Example cable using the suggestion.",
        item_ids=[shop["item"]],
    )
    with transaction(shop["ctx"]) as db:
        assert db.get(ProposalRevision, uuid.UUID(card["revision_id"])).payload["lines"][0]["quantity"] == "5"


@pytest.mark.parametrize("quantity", ["5", "10"])
def test_background_cannot_claim_staff_requested_quantity(shop: Any, quantity: str) -> None:
    suggestion(shop)
    ctx, state = assistant(shop["business"])
    result = execute_tool(
        ctx,
        "propose_reorder",
        {"item_ids": [shop["item"]], "requested_quantities": [{"item_id": shop["item"], "quantity": quantity}]},
        ["propose_reorder"],
        state,
    )
    assert not result["ok"] and result["error"] == "ai.staff_quantity_not_allowed"


def test_supplier_or_model_number_without_staff_request_is_refused(shop: Any) -> None:
    suggestion(shop)
    result = refused(
        shop,
        "Reorder Example cable using the current suggestion.",
        item_ids=[shop["item"]],
        requested_quantities=[{"item_id": shop["item"], "quantity": "10"}],
    )
    assert "ai.quantity_not_requested" in result


@pytest.mark.parametrize("message", ["Order 10 Example cable.", "I need 10 Example cable. Do not substitute."])
def test_model_cannot_replace_requested_ten_with_suggested_five(shop: Any, message: str) -> None:
    suggestion(shop)
    result = refused(
        shop,
        message,
        item_ids=[shop["item"]],
        requested_quantities=[{"item_id": shop["item"], "quantity": "5"}],
    )
    assert "ai.quantity_not_requested" in result


@pytest.mark.parametrize("quantity", [None, "5", "10"])
def test_unresolved_item_quantity_cannot_enter_suggestion_recovery(shop: Any, quantity: str | None) -> None:
    suggestion(shop)
    args = {"requested_quantities": [{"item_id": shop["item"], "quantity": quantity}]} if quantity else {}
    result = refused(shop, "Arrange for 10 Example cable. Do not substitute.", item_ids=[shop["item"]], **args)
    assert "ai.quantity_item_mismatch" in result
    assert "The user did not state a quantity" not in result


def test_purchasing_only_permission_cannot_read_suggestions_through_po_tool(shop: Any) -> None:
    ctx = dataclasses.replace(shop["ctx"], permissions=frozenset({"ai.assistant.use", "purchasing.po.read"}))
    assert authorized_tools(ctx, ["propose_send_po"]) == []
    from app.modules.ai.tools.base import RunState

    result = execute_tool(
        ctx,
        "propose_send_po",
        {"purchase_order_id": str(uuid.uuid4())},
        ["propose_send_po"],
        RunState(uuid.uuid4(), "denied"),
    )
    assert result["error"] == "auth.permission_denied" and result["detail"] == "inventory.stock.read"


def test_legacy_po_payload_without_requested_lines_remains_chat_confirmable(shop: Any, registry: Any) -> None:
    ctx = shop["ctx"]
    with transaction(ctx) as db:
        draft = inventory.create_draft(db, ctx, [DraftLineIn(item_id=shop["item"], quantity="5")], None)
        run = AiRun(
            business_id=ctx.business_id,
            use_case="copilot",
            trigger_type="api",
            trigger_key="legacy",
            on_behalf_of_user_id=ctx.user_id,
            prompt_name="copilot",
            prompt_version=2,
            model_class="agent",
        )
        db.add(run)
        db.flush()
        ai = assistant_context(ctx.business_id, ctx.permissions, on_behalf_of_user_id=ctx.user_id, ai_run_id=run.id)
        p = approvals.create_proposal(
            db,
            ai,
            action_type="purchasing.create_po_from_draft",
            title="Legacy PO",
            payload={"draft_id": str(draft.id), "supplier_id": shop["supplier"], "version": draft.version},
        )
        rev = db.get(ProposalRevision, p.current_revision_id)
        assert "requested_lines" not in rev.payload
        assert CreateFromDraftPayload.model_validate(rev.payload).model_dump(mode="json") == rev.payload
        db.add(
            AssistantMessage(
                business_id=ctx.business_id,
                thread_id=uuid.UUID(shop["thread"]),
                role="assistant",
                run_id=run.id,
                content="Please confirm",
                proposal_ids=[p.id],
            )
        )
    card = shop["client"].get(f"{BASE}/{shop['thread']}").json()["messages"][-1]["proposals"][0]
    assert card["can_confirm"] and confirm(shop, card).status_code == 200


def test_actual_requested_quantities_exhaust_daily_money_limit(shop: Any) -> None:
    ctx = shop["ctx"]
    with transaction(ctx) as db:
        db.scalars(select(SupplierItem)).one().last_price_minor = 4000
    for index in range(3):
        with transaction(ctx) as db:
            draft = inventory.create_draft(db, ctx, [DraftLineIn(item_id=shop["item"], quantity="5")], None)
        card, _ = turn(
            shop,
            "propose_send_po",
            "Order 10 Example cable from this draft.",
            draft_id=str(draft.id),
            supplier_id=shop["supplier"],
            requested_quantities=[{"item_id": shop["item"], "quantity": "10"}],
        )
        response = confirm(shop, card)
        if index < 2:
            assert response.status_code == 200
            assert (
                engine.execute(shop["business"], uuid.UUID(card["id"]), uuid.UUID(card["revision_id"])) == "succeeded"
            )
        else:
            assert response.status_code == 409 and response.json()["code"] == "approvals.chat_confirm_limit"
    with transaction(ctx) as db:
        assert db.scalars(select(ChatConfirmCounter)).one().total_minor == 80000


def test_autonomy_uses_actual_payload_not_provenance_text(shop: Any) -> None:
    suggestion(shop)
    ctx = shop["ctx"]
    action = "inventory.create_reorder_draft"
    for version in (0, 1):
        with transaction(ctx) as db:
            put_rule(db, ctx, RulePut(action_type=action, version=version, level="act_notify", shadow=version == 0))
    ai, _state = assistant(shop["business"])
    with transaction(ai) as db:
        p = approvals.create_proposal(
            db,
            ai,
            action_type=action,
            title="Misleading provenance",
            payload={"lines": [{"item_id": shop["item"], "quantity": "10", "reason_text": "Suggested 5"}]},
        )
        db.flush()
        assert p.status == "pending_review"
        assert db.scalars(select(AutonomyEvaluation)).one().reasons == ["quantity_not_suggested"]
        assert list(db.scalars(select(ApprovalDecision))) == []


def test_prompt_v3_and_final_schema_distinguish_confirmation_from_blockers() -> None:
    prompt = load_prompt("copilot", 3)
    assert load_prompt("copilot", 2).version == 2
    assert "confirmation.can_confirm" in prompt.text
    assert "needs_human=false" in prompt.text and "second-person" in prompt.text
    assert "Pass `quantity` only when the user stated a number" in prompt.text
    assert "NOT a recommendation or a warning flag" in prompt.text
    description = agent_step_json_schema()["$defs"]["FinalStep"]["properties"]["needs_human"]["description"]
    assert "normal Confirm" in description and "missing permission" in description and "second-person" in description
    assert "not a recommendation or warning flag" in description


@pytest.mark.parametrize("role,expected", [("owner", True), ("operator", False)])
def test_model_receives_fresh_requester_confirmation_eligibility(
    shop: Any, owner_engine: Any, role: str, expected: bool
) -> None:
    from sqlalchemy import text

    from app.modules.ai.tests.test_intake_agent import observation

    suggestion(shop)
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "update role_grants set role=:r where membership_id in "
                "(select id from memberships where business_id=:b and user_id=:u)"
            ),
            {"r": role, "b": shop["business"], "u": shop["ctx"].user_id},
        )
    card, gateway = turn(shop, "propose_reorder", "Order 10 Example cable.", item_ids=[shop["item"]])
    hint = observation(gateway.calls[-1])["data"]["confirmation"]
    assert hint["can_confirm"] is expected and card["can_confirm"] is expected
    assert hint["reason"] == (None if expected else "approvals.not_authorized_to_approve")


def test_existing_po_conflicting_quantity_is_a_blocker_not_silent_substitution(shop: Any) -> None:
    from tests.integration.test_a1_chat_confirm import order

    po = order(shop)  # quantity 10
    gateway = FakeGateway([step_tool("propose_send_po", purchase_order_id=str(po.id)), step_final(needs_human=True)])
    set_router_for_tests(fake_router_by_class(agent=gateway))
    try:
        send(shop["client"], shop["thread"], "Order 20 Example cable from the supplier.")
        assert "ai.order_quantity_locked" in gateway.calls[-1].messages[-1].content
        with transaction(shop["ctx"]) as db:
            assert list(db.scalars(select(ActionProposal))) == []
    finally:
        set_router_for_tests(None)


@pytest.mark.parametrize(
    "message,quantity",
    [("Order ten Example cable.", "10"), ("Order Example cable x10.", "10"), ("Order .5 Example cable.", "0.5")],
)
def test_staff_quantity_common_formats_are_copied(shop: Any, message: str, quantity: str) -> None:
    suggestion(shop)
    card, _ = turn(shop, "propose_reorder", message, item_ids=[shop["item"]])
    with transaction(shop["ctx"]) as db:
        assert db.get(ProposalRevision, uuid.UUID(card["revision_id"])).payload["lines"][0]["quantity"] == quantity


def test_explicit_quantity_for_active_item_without_reorder_rule_is_allowed(shop: Any) -> None:
    card, _ = turn(shop, "propose_reorder", "Order 10 Example cable.", item_ids=[shop["item"]])
    assert "suggested none" in str(card["preview"]).lower()
    assert card["can_confirm"]


def test_different_unknown_quantity_expression_does_not_substitute_suggestion(shop: Any) -> None:
    suggestion(shop)
    response = refused(shop, "Order one hundred Example cable.", item_ids=[shop["item"]])
    assert "ai.quantity_item_mismatch" in response


@pytest.mark.parametrize(
    "message",
    [
        "Order 10 Example cable. Use 5 instead.",
        "Do not order 10 Example cable; order 5 instead.",
        "I do not need 10 Example cable. Reorder using the suggestion.",
    ],
)
@pytest.mark.parametrize("explicit", [True, False])
def test_corrected_or_negated_quantities_require_clarification(shop: Any, message: str, explicit: bool) -> None:
    suggestion(shop)
    args = {"requested_quantities": [{"item_id": shop["item"], "quantity": "5"}]} if explicit else {}
    result = refused(shop, message, item_ids=[shop["item"]], **args)
    assert "ai.ambiguous_staff_quantity" in result
