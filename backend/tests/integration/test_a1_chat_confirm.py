"""A1 security and atomic daily reservations through real sessions and PostgreSQL."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from app.main import create_app
from app.modules.ai.models import AiRun, AssistantMessage
from app.modules.ai.tests.test_copilot import new_thread
from app.modules.ai.usecases import copilot_cards
from app.modules.approvals import engine
from app.modules.approvals import service as approvals
from app.modules.approvals.models import ActionProposal, ApprovalDecision, ChatConfirmCounter
from app.modules.approvals.public import ChatConfirmIn
from app.modules.identity import public as identity
from app.modules.identity.testing import add_member, login_as, make_user
from app.modules.notifications.models import OutboxMessage
from app.modules.org.testing import make_business
from app.modules.purchasing import service
from app.modules.purchasing.schemas import POCreate, POLineIn
from app.platform import clock
from app.platform.context import Context, assistant_context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.registry import Registry

BASE = "/api/v1/ai/copilot/threads"


@pytest.fixture
def shop(registry: Registry) -> Any:
    clock.set_clock(lambda: datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    business = make_business()
    user = make_user()
    add_member(user, business, ["owner"])
    ctx = Context("test", "user", user, business, identity.ROLES["owner"])
    app = create_app(registry)
    client = login_as(app, business, ["owner"], user_id=user)
    item = client.post(
        "/api/v1/catalog/items", json={"name": "Example cable", "kind": "product", "is_stock_tracked": True}
    ).json()["id"]
    supplier = client.post(
        "/api/v1/purchasing/suppliers",
        json={
            "name": "Example Parts",
            "email": "parts@example.com",
            "items": [{"catalog_item_id": item, "last_price_minor": 1200}],
        },
    ).json()["id"]
    tid = new_thread(client)
    return dict(ctx=ctx, business=business, app=app, client=client, item=item, supplier=supplier, thread=tid)


def order(shop: Any, *, cost: int = 1200, sent: bool = False) -> Any:
    ctx = shop["ctx"]
    with transaction(ctx) as db:
        row = service.create(
            db,
            ctx,
            POCreate(
                supplier_id=shop["supplier"],
                lines=[POLineIn(catalog_item_id=shop["item"], quantity="10", unit_cost_minor=cost)],
            ),
        )
        row = service.transition(db, ctx, row.id, row.version, "approve")
        if sent:
            row = service.transition(db, ctx, row.id, row.version, "send")
        return row


def proposal(shop: Any, po: Any, *, thread: str | None = None, origin_user: uuid.UUID | None = None) -> dict[str, Any]:
    ctx = shop["ctx"]
    with transaction(ctx) as db:
        run = AiRun(
            business_id=ctx.business_id,
            use_case="copilot",
            trigger_type="api",
            trigger_key="copilot:test",
            on_behalf_of_user_id=ctx.user_id,
            prompt_name="copilot",
            prompt_version=2,
            model_class="agent",
            status="succeeded",
        )
        db.add(run)
        db.flush()
        ai = assistant_context(
            ctx.business_id,
            frozenset({"ai.assistant.use"}),
            on_behalf_of_user_id=origin_user or ctx.user_id,
            ai_run_id=run.id,
        )
        row = approvals.create_proposal(
            db,
            ai,
            action_type="purchasing.send_po",
            title="Send Example Parts PO",
            payload={"purchase_order_id": str(po.id), "version": po.version},
        )
        db.add(
            AssistantMessage(
                business_id=ctx.business_id,
                thread_id=uuid.UUID(thread or shop["thread"]),
                role="assistant",
                content="Please confirm.",
                run_id=run.id,
                proposal_ids=[row.id],
            )
        )
        rev = approvals.get_detail(db, ctx, row.id)["current_revision"]
        return dict(id=row.id, run_id=run.id, revision_id=rev.id, payload_hash=rev.payload_hash)


def confirm(shop: Any, p: dict[str, Any], *, thread: str | None = None, **changes: Any) -> Any:
    body = {"revision_id": str(p["revision_id"]), "payload_hash": p["payload_hash"], **changes}
    return shop["client"].post(f"{BASE}/{thread or shop['thread']}/proposals/{p['id']}/confirm", json=body)


def test_chat_confirm_marker_and_normal_self_approval_still_blocked(shop: Any) -> None:
    order(shop, sent=True)
    p = proposal(shop, order(shop))
    normal = shop["client"].post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": str(p["revision_id"]), "payload_hash": p["payload_hash"]},
    )
    assert normal.status_code == 403 and normal.json()["code"] == "approvals.self_approval_forbidden"
    detail = shop["client"].get(f"{BASE}/{shop['thread']}").json()
    card = detail["messages"][-1]["proposals"][0]
    assert card["can_confirm"] and card["title"] and card["preview"]
    assert card["action_type"] == "purchasing.send_po" and card["status"] == "pending_review"
    result = confirm(shop, p)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "approved" and not result.json()["can_confirm"]
    with transaction(shop["ctx"]) as db:
        decision = db.scalars(select(ApprovalDecision)).one()
        assert (decision.decided_by_kind, decision.decided_via, decision.decided_by) == (
            "user",
            "chat",
            shop["ctx"].user_id,
        )
    assert engine.execute(shop["business"], p["id"], p["revision_id"]) == "succeeded"
    with transaction(shop["ctx"]) as db:
        m = db.scalars(select(OutboxMessage)).one()
        assert m.not_before == clock.now() + timedelta(seconds=120)
    assert shop["client"].get(f"/api/v1/approvals/proposals/{p['id']}").json()["delivery_state"] == "scheduled"


@pytest.mark.parametrize("case", ["large", "currency", "new"])
def test_second_person_refused_in_chat_stays_pending(shop: Any, case: str) -> None:
    if case != "new":
        order(shop, sent=True)
    po = order(shop, cost=5001 if case == "large" else 1200)
    if case == "currency":
        with transaction(shop["ctx"]) as db:
            service.repo.order(db, shop["ctx"], po.id).currency = "EUR"
    p = proposal(shop, po)
    result = confirm(shop, p)
    assert result.status_code == 403 and result.json()["code"] == "approvals.second_person_required"
    card = shop["client"].get(f"{BASE}/{shop['thread']}").json()["messages"][-1]["proposals"][0]
    assert card["status"] == "pending_review" and not card["can_confirm"]
    assert card["cannot_confirm_reason"] == "approvals.second_person_required"


@pytest.mark.parametrize("case", ["other_thread", "other_user", "forged_origin", "run_user", "run_kind", "no_link"])
def test_confirm_provenance_cannot_cross_user_or_thread(shop: Any, case: str, registry: Registry) -> None:
    order(shop, sent=True)
    p = proposal(shop, order(shop))
    target = shop["thread"]
    if case == "other_thread":
        target = new_thread(shop["client"])
    elif case == "other_user":
        shop["client"] = login_as(shop["app"], shop["business"], ["owner"])
    else:
        with transaction(shop["ctx"]) as db:
            if case == "forged_origin":
                db.get(ActionProposal, p["id"]).origin_user_id = uuid.uuid4()
            elif case == "run_user":
                db.get(AiRun, p["run_id"]).on_behalf_of_user_id = uuid.uuid4()
            elif case == "run_kind":
                db.get(AiRun, p["run_id"]).use_case = "procurement"
            else:
                db.get(ActionProposal, p["id"]).ai_run_id = uuid.uuid4()
    assert confirm(shop, p, thread=target).status_code == 404


@pytest.mark.parametrize("changes", [{"revision_id": str(uuid.uuid4())}, {"payload_hash": "0" * 64}])
def test_confirm_revision_or_hash_mismatch_409(shop: Any, changes: dict[str, str]) -> None:
    order(shop, sent=True)
    p = proposal(shop, order(shop))
    result = confirm(shop, p, **changes)
    assert result.status_code == 409 and result.json()["code"] == "approvals.revision_changed"


def test_missing_current_approve_permission_403(shop: Any, owner_engine: Any) -> None:
    order(shop, sent=True)
    p = proposal(shop, order(shop))
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "update role_grants set role='operator' where membership_id in "
                "(select id from memberships where user_id=:u and business_id=:b)"
            ),
            {"u": shop["ctx"].user_id, "b": shop["business"]},
        )
    response = confirm(shop, p)
    assert response.status_code == 403 and response.json()["code"] == "approvals.not_authorized_to_approve"
    # Stale supplied permissions cannot bypass the fresh identity check.
    with pytest.raises(DomainError) as exc:
        copilot_cards.confirm(
            shop["ctx"],
            uuid.UUID(shop["thread"]),
            p["id"],
            ChatConfirmIn(**{k: p[k] for k in ("revision_id", "payload_hash")}),
        )
    assert exc.value.status == 403 and exc.value.code == "approvals.not_authorized_to_approve"


def test_concurrent_daily_money_limit_and_business_local_reset(shop: Any) -> None:
    order(shop, sent=True)
    candidates = [proposal(shop, order(shop, cost=5000)) for _ in range(6)]

    def attempt(p):
        try:
            copilot_cards.confirm(
                shop["ctx"],
                uuid.UUID(shop["thread"]),
                p["id"],
                ChatConfirmIn(revision_id=p["revision_id"], payload_hash=p["payload_hash"]),
            )
            return "approved"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, candidates))
    assert results.count("approved") == 2 and results.count("approvals.chat_confirm_limit") == 4
    with transaction(shop["ctx"]) as db:
        assert db.scalars(select(ChatConfirmCounter)).one().total_minor == 100000
        assert len(list(db.scalars(select(ApprovalDecision)))) == 2
    # Denver's local day is still Nov 5 at 01:00 UTC Nov 6
    # Reset at local midnight, 07:00 UTC.
    clock.set_clock(lambda: datetime(2026, 11, 6, 1, tzinfo=UTC))
    assert (
        attempt(next(p for p, r in zip(candidates, results, strict=True) if r != "approved"))
        == "approvals.chat_confirm_limit"
    )
    clock.set_clock(lambda: datetime(2026, 11, 6, 7, tzinfo=UTC))
    assert attempt(next(p for p, r in zip(candidates, results, strict=True) if r != "approved")) == "approved"


def test_two_concurrent_clicks_on_same_card_decide_once(shop: Any) -> None:
    from threading import Barrier

    order(shop, sent=True)
    p = proposal(shop, order(shop))
    barrier = Barrier(2)

    def click():
        barrier.wait(timeout=5)
        try:
            copilot_cards.confirm(
                shop["ctx"],
                uuid.UUID(shop["thread"]),
                p["id"],
                ChatConfirmIn(revision_id=p["revision_id"], payload_hash=p["payload_hash"]),
            )
            return "approved"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: click(), range(2)))
    assert sorted(results) == ["approvals.not_pending", "approved"]
    with transaction(shop["ctx"]) as db:
        assert len(list(db.scalars(select(ApprovalDecision)))) == 1
        assert db.scalars(select(ChatConfirmCounter)).one().total_minor == 12000


def test_cached_revision_cannot_confirm_after_competing_revision(shop: Any) -> None:
    from app.modules.approvals import chat

    order(shop, sent=True)
    po = order(shop)
    p = proposal(shop, po)

    def revise():
        with transaction(shop["ctx"]) as db:
            approvals.revise(
                db, shop["ctx"], p["id"], p["revision_id"], {"purchase_order_id": str(po.id), "version": po.version}
            )

    with pytest.raises(DomainError) as exc, transaction(shop["ctx"]) as db:
        cached = chat.owned_proposal(db, shop["ctx"], p["id"])
        assert cached.current_revision_id == p["revision_id"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(revise).result(timeout=5)
        chat.confirm(
            db, shop["ctx"], p["id"], run_id=p["run_id"], revision_id=p["revision_id"], payload_hash=p["payload_hash"]
        )
    assert exc.value.code == "approvals.revision_changed"
