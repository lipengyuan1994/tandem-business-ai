"""A1 purchasing tool and fake-model chain integration, with no provider calls."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.ai.models import AiRun, AssistantMessage
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_copilot import send
from app.modules.approvals import engine
from app.modules.approvals import service as approvals
from app.modules.approvals.models import ApprovalDecision, ChatConfirmCounter
from app.modules.notifications.models import OutboxMessage
from app.platform.context import assistant_context
from app.platform.db import transaction
from app.platform.registry import Registry
from tests.integration.test_a1_chat_confirm import BASE, confirm, order
from tests.integration.test_a1_chat_confirm import shop as shop


def test_fake_order_ten_produces_confirmable_reorder(shop: Any) -> None:
    client = shop["client"]
    assert (
        client.put(
            f"/api/v1/inventory/reorder-rules/{shop['item']}", json={"reorder_point": "2", "target_level": "10"}
        ).status_code
        == 200
    )
    gateway = FakeGateway(
        [
            step_tool("list_low_stock"),
            step_tool("propose_reorder", item_ids=[shop["item"]]),
            step_final("Click Confirm to create the reorder draft. Then create, approve and send the PO."),
        ]
    )
    set_router_for_tests(fake_router_by_class(agent=gateway))
    try:
        events = send(client, shop["thread"], "Order 10 of Example cable from the supplier.")
        assert events[-1]["kind"] == "final" and any(e["kind"] == "proposal" for e in events)
        card = client.get(f"{BASE}/{shop['thread']}").json()["messages"][-1]["proposals"][0]
        assert card["action_type"] == "inventory.create_reorder_draft" and card["can_confirm"]
        assert "propose_send_po" in gateway.calls[0].messages[0].content
        assert "propose_quote_request" in gateway.calls[0].messages[0].content
        response = client.post(
            f"{BASE}/{shop['thread']}/proposals/{card['id']}/confirm",
            json={"revision_id": card["revision_id"], "payload_hash": card["payload_hash"]},
        )
        assert response.status_code == 200, response.text
    finally:
        set_router_for_tests(None)


def test_full_copilot_reorder_create_approve_send_chain_reserves_order_once(shop: Any) -> None:
    from app.modules.ai.usecases import copilot

    order(shop, sent=True)
    shop["client"].put(
        f"/api/v1/inventory/reorder-rules/{shop['item']}", json={"reorder_point": "2", "target_level": "10"}
    )

    def turn(tool, **arguments):
        gateway = FakeGateway([step_tool(tool, **arguments), step_final("Please click the action card.")])
        set_router_for_tests(fake_router_by_class(agent=gateway))
        events = send(shop["client"], shop["thread"], "Continue ordering 10 Example cable from Example Parts.")
        assert events[-1]["kind"] == "final" and any(e["kind"] == "proposal" for e in events)
        card = shop["client"].get(f"{BASE}/{shop['thread']}").json()["messages"][-1]["proposals"][0]
        assert card["can_confirm"], card
        p = dict(
            id=uuid.UUID(card["id"]), revision_id=uuid.UUID(card["revision_id"]), payload_hash=card["payload_hash"]
        )
        assert confirm(shop, p).status_code == 200
        assert engine.execute(shop["business"], p["id"], p["revision_id"]) == "succeeded"
        saved = shop["client"].get(f"{BASE}/{shop['thread']}").json()["messages"][-1]["proposals"][0]
        return saved

    try:
        reorder = turn("propose_reorder", item_ids=[shop["item"]])
        draft_id = reorder["result"]["reorder_draft_id"]
        created = turn("propose_send_po", draft_id=draft_id, supplier_id=shop["supplier"])
        assert created["action_type"] == "purchasing.create_po_from_draft"
        po_id = created["result"]["purchase_order_id"]
        approved = turn("propose_send_po", purchase_order_id=po_id)
        assert approved["action_type"] == "purchasing.approve_po"
        delivered = turn("propose_send_po", purchase_order_id=po_id)
        assert delivered["action_type"] == "purchasing.send_po" and delivered["delivery_state"] == "scheduled"
        # Current DB result refs reach the next model turn without asking the person to find IDs.
        prepared = copilot.prepare_message(
            shop["ctx"], uuid.UUID(shop["thread"]), copilot.MessageCreate(content="What is waiting?")
        )
        try:
            assert any(po_id in block.text for block in prepared.spec.context_blocks)
        finally:
            copilot.release_generation(prepared)
        with transaction(shop["ctx"]) as db:
            assert db.scalars(select(ChatConfirmCounter)).one().total_minor == 12000
            assert len(list(db.scalars(select(OutboxMessage)))) == 1
            decisions = list(db.scalars(select(ApprovalDecision)))
            assert len(decisions) == 4 and all(d.decided_via == "chat" for d in decisions)
    finally:
        set_router_for_tests(None)


@pytest.mark.parametrize(
    "case_id",
    ["copilot-order-cables", "copilot-order-quantity-mismatch", "copilot-order-suggested", "copilot-typed-yes"],
)
def test_new_copilot_eval_cases_with_fake_gateway(case_id: str, owner_engine: Any) -> None:
    from app.modules.ai.gateway.fake import fake_router
    from evals.runner import load_cases, run_case
    from evals.runtime import LiveRuntime

    case = next(case for case in load_cases("copilot") if case.id == case_id)
    calls = 0

    def model(_):
        nonlocal calls
        calls += 1
        if case_id != "copilot-typed-yes" and calls == 1:
            return step_tool("list_low_stock")
        if case_id != "copilot-typed-yes" and calls == 2:
            with owner_engine.connect() as db:
                item = str(db.scalar(text("select id from catalog_items where name='Cable set' limit 1")))
            quantity = {"copilot-order-quantity-mismatch": "10", "copilot-order-suggested": "5"}.get(case_id)
            requested = [{"item_id": item, "quantity": quantity}] if quantity is not None else []
            return step_tool("propose_reorder", item_ids=[item], requested_quantities=requested)
        return step_final(
            "Please use the action card; explicit staff quantities are preserved.",
            needs_human=case_id == "copilot-typed-yes",
        )

    gateway = FakeGateway(model)
    runtime = LiveRuntime("copilot", router=fake_router(gateway), owner_engine=owner_engine)
    scored = run_case(case, runtime.execute)
    assert scored.passed, scored.failures


def test_chat_po_creation_cannot_bypass_per_order_money_limit(shop: Any) -> None:
    from app.modules.ai.tools.base import RunState
    from app.modules.ai.tools.orders import SendPOArgs, run
    from app.modules.inventory import service as inventory
    from app.modules.inventory.schemas import DraftLineIn
    from app.modules.purchasing.models import SupplierItem

    ctx = shop["ctx"]
    with transaction(ctx) as db:
        item = db.scalars(select(SupplierItem)).one()
        item.last_price_minor = 6000
        draft = inventory.create_draft(db, ctx, [DraftLineIn(item_id=shop["item"], quantity="10")], None)
        run_id = uuid.uuid4()
        db.add(
            AiRun(
                id=run_id,
                business_id=ctx.business_id,
                use_case="copilot",
                trigger_type="api",
                trigger_key="cap",
                on_behalf_of_user_id=ctx.user_id,
                prompt_name="copilot",
                prompt_version=2,
                model_class="agent",
            )
        )
        ai = assistant_context(ctx.business_id, ctx.permissions, on_behalf_of_user_id=ctx.user_id, ai_run_id=run_id)
        p = run(db, ai, SendPOArgs(draft_id=draft.id, supplier_id=shop["supplier"]), RunState(run_id, "cap"))
        db.add(
            AssistantMessage(
                business_id=ctx.business_id,
                thread_id=uuid.UUID(shop["thread"]),
                role="assistant",
                content="Create PO",
                run_id=run_id,
                proposal_ids=[p["proposal_id"]],
            )
        )
        rev = approvals.get_detail(db, ctx, p["proposal_id"])["current_revision"]
    response = confirm(shop, {"id": p["proposal_id"], "revision_id": rev.id, "payload_hash": rev.payload_hash})
    assert response.status_code == 403 and response.json()["code"] == "approvals.second_person_required"


def test_new_order_tool_requires_lookup_permission_and_campaign_second_person(registry: Registry, shop: Any) -> None:
    from app.modules.ai.tools.base import RunState
    from app.modules.ai.tools.registry import authorized_tools, execute_tool

    ctx = dataclasses.replace(shop["ctx"], permissions=frozenset({"ai.assistant.use"}))
    names = [
        "propose_send_po",
        "propose_quote_request",
        "propose_supplier_quote",
        "propose_po_from_quote",
        "propose_supplier_followup",
    ]
    assert authorized_tools(ctx, names) == []
    assert (
        execute_tool(
            ctx, "propose_send_po", {"purchase_order_id": str(uuid.uuid4())}, names, RunState(uuid.uuid4(), "denied")
        )["error"]
        == "auth.permission_denied"
    )
    ctx = dataclasses.replace(ctx, permissions=ctx.permissions | {"purchasing.po.read", "inventory.stock.read"})
    assert authorized_tools(ctx, names) == names
    assert registry.executors["marketing.approve_campaign"].second_person is True


@pytest.mark.parametrize("cost,status", [(1200, 200), (6000, 403)])
def test_quote_po_creation_uses_chat_money_checks(shop: Any, cost: int, status: int) -> None:
    from datetime import timedelta

    from app.modules.purchasing import quote_service
    from app.modules.purchasing.schemas_quotes import RecordSupplierQuote, SendQuoteRequests
    from app.platform import clock

    ctx = shop["ctx"]
    with transaction(ctx) as db:
        request = quote_service.send_requests(
            db,
            ctx,
            SendQuoteRequests(
                lines=[{"item_id": shop["item"], "quantity": "10"}],
                supplier_ids=[shop["supplier"]],
                reply_by=clock.now().date(),
                needed_by=clock.now().date() + timedelta(days=7),
            ),
            "quote-chat",
        )
        quote = quote_service.record_quote(
            db,
            ctx,
            RecordSupplierQuote(
                quote_request_id=request.id,
                supplier_id=shop["supplier"],
                currency="USD",
                lead_time_days=2,
                source="staff",
                lines=[{"item_id": shop["item"], "unit_price_minor": cost}],
            ),
        )
        run_id = uuid.uuid4()
        db.add(
            AiRun(
                id=run_id,
                business_id=ctx.business_id,
                use_case="copilot",
                trigger_type="api",
                trigger_key="quote",
                on_behalf_of_user_id=ctx.user_id,
                prompt_name="copilot",
                prompt_version=2,
                model_class="agent",
            )
        )
        ai = assistant_context(ctx.business_id, ctx.permissions, on_behalf_of_user_id=ctx.user_id, ai_run_id=run_id)
        p = approvals.create_proposal(
            db,
            ai,
            action_type="purchasing.create_po_from_quote",
            title="Create quote PO",
            payload={"quote_id": str(quote.id), "quote_version": quote.version},
        )
        db.add(
            AssistantMessage(
                business_id=ctx.business_id,
                thread_id=uuid.UUID(shop["thread"]),
                role="assistant",
                run_id=run_id,
                content="Please confirm",
                proposal_ids=[p.id],
            )
        )
        rev = approvals.get_detail(db, ctx, p.id)["current_revision"]
    response = confirm(shop, {"id": p.id, "revision_id": rev.id, "payload_hash": rev.payload_hash})
    assert response.status_code == status, response.text
    if status == 403:
        assert response.json()["code"] == "approvals.second_person_required"
    else:
        with transaction(ctx) as db:
            assert db.scalars(select(ChatConfirmCounter)).one().total_minor == 12000
