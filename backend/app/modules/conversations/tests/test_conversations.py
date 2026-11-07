"""M04 conversations tests (docs/roadmap/modules/M04-conversations.md#tests-must-exist)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import events, jobs
from app.platform.registry import Registry

BASE = "https://testserver"


@pytest.fixture
def biz() -> uuid.UUID:
    return make_business()


@pytest.fixture
def app(registry: Registry, biz: uuid.UUID) -> Any:
    return create_app(registry)


@pytest.fixture
def staff(app: Any, biz: uuid.UUID) -> Any:
    return lambda roles=("operator",), business=None: login_as(app, business or biz, list(roles))


def visitor(app: Any) -> TestClient:
    return TestClient(app, base_url=BASE)


def _start(
    v: TestClient, slug: str = "example-cycle-works", msg: str = "Can I bring my bike in Tuesday morning?"
) -> Any:
    return v.post(f"/api/v1/public/{slug}/chat", json={"name": "Ada", "email": "ada@example.com", "message": msg})


def test_visitor_chat_flow_and_cookie_scope(app: Any, staff: Any) -> None:
    v = visitor(app)
    r = _start(v)
    assert r.status_code == 201 and r.json()["messages"][0]["body"].startswith("Can I")
    cookie = r.headers["set-cookie"]
    assert "Path=/api/v1/public/example-cycle-works/" in cookie and "HttpOnly" in cookie and "Secure" in cookie
    assert (
        v.post("/api/v1/public/example-cycle-works/chat/messages", json={"body": "Also my chain squeaks."}).status_code
        == 201
    )
    inbox = staff().get("/api/v1/conversations", params={"status": "waiting_on_staff"}).json()["items"]
    assert inbox[0]["visitor_name"] == "Ada" and inbox[0]["last_message_preview"] == "Also my chain squeaks."


def test_visitor_cannot_read_other_conversation_even_with_id(app: Any) -> None:
    a, b = visitor(app), visitor(app)
    _start(a)
    _start(b, msg="Different person")
    assert [m["body"] for m in b.get("/api/v1/public/example-cycle-works/chat").json()["messages"]] == [
        "Different person"
    ]
    stranger = visitor(app)
    assert stranger.get("/api/v1/public/example-cycle-works/chat").status_code == 403
    stranger.cookies.set(
        "smb_visitor", "guessed-token", domain="testserver.local", path="/api/v1/public/example-cycle-works/"
    )
    assert stranger.get("/api/v1/public/example-cycle-works/chat").status_code == 403


def test_visitor_token_is_bound_to_its_business(app: Any) -> None:
    make_business("example-garden-store")
    v = visitor(app)
    token = _start(v).cookies.get("smb_visitor")
    other = visitor(app)
    other.cookies.set(
        "smb_visitor", token or "", domain="testserver.local", path="/api/v1/public/example-garden-store/"
    )
    assert other.get("/api/v1/public/example-garden-store/chat").status_code == 403


def test_internal_notes_hidden_from_visitor(app: Any, staff: Any) -> None:
    v = visitor(app)
    conv_id = _start(v).json()["conversation_id"]
    s = staff()
    s.post(
        f"/api/v1/conversations/{conv_id}/messages", json={"direction": "internal", "body": "Probably needs new pads"}
    )
    s.post(
        f"/api/v1/conversations/{conv_id}/messages", json={"direction": "outbound", "body": "Yes, Tuesday 11:45 works!"}
    )
    bodies = [
        (m["from_business"], m["body"]) for m in v.get("/api/v1/public/example-cycle-works/chat").json()["messages"]
    ]
    assert bodies == [(False, "Can I bring my bike in Tuesday morning?"), (True, "Yes, Tuesday 11:45 works!")]


def test_inbound_message_publishes_event_once(app: Any, owner_engine: Engine) -> None:
    v = visitor(app)
    _start(v)
    v.post("/api/v1/public/example-cycle-works/chat/messages", json={"body": "hello?"})
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "select payload->>'is_first_message' from domain_events"
                    " where type = 'conversations.message_received' order by occurred_at"
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["true", "false"]


def test_get_message_context_fields_and_isolation(app: Any, biz: uuid.UUID, staff: Any) -> None:
    from datetime import datetime

    from app.modules.conversations import public as conversations
    from app.platform.db import transaction
    from app.platform.errors import Forbidden, NotFound
    from app.platform.ids import new_id
    from app.platform.testing.auth import ctx_for

    response = visitor(app).post(
        CHAT,
        json={"name": "Ada Example", "email": "ada@example.com", "phone": "+12025550123", "message": "Brake service?"},
    )
    assert response.status_code == 201, response.text
    thread = response.json()
    message = thread["messages"][0]
    ctx = ctx_for(biz, ["conversations.thread.read"])
    with transaction(ctx) as db:
        result = conversations.get_message_context(db, ctx, uuid.UUID(message["id"]))
        assert result.model_dump() == {
            "conversation_id": uuid.UUID(thread["conversation_id"]),
            "channel": "web_chat",
            "author_kind": "visitor",
            "body": "Brake service?",
            "customer_id": None,
            "visitor_name": "Ada Example",
            "visitor_email": "ada@example.com",
            "visitor_phone": "+12025550123",
            "created_at": datetime.fromisoformat(message["created_at"].replace("Z", "+00:00")),
            "ai_mode": "auto",
            "ai_generation": 0,
            "message_ai_generation": 0,
            "emergency": False,
        }
        with pytest.raises(NotFound):
            conversations.get_message_context(db, ctx, new_id())
    customer = staff(["owner"]).post("/api/v1/crm/customers", json={"display_name": "Ada Example"}).json()["id"]
    writer = ctx_for(biz, ["conversations.thread.write", "crm.customer.read"])
    with transaction(writer) as db:
        conversations.link_customer(db, writer, result.conversation_id, uuid.UUID(customer))
    with transaction(ctx) as db:
        assert conversations.get_message_context(db, ctx, uuid.UUID(message["id"])).customer_id == uuid.UUID(customer)
    other = ctx_for(make_business("other-shop"), ["conversations.thread.read"])
    with transaction(other) as db, pytest.raises(NotFound):
        conversations.get_message_context(db, other, uuid.UUID(message["id"]))
    with transaction(ctx_for(biz)) as db, pytest.raises(Forbidden):
        conversations.get_message_context(db, ctx_for(biz), uuid.UUID(message["id"]))


def test_public_rate_limits_enforced(app: Any, freeze_time: Any) -> None:
    from datetime import UTC, datetime

    # One fixed instant: a fixed-window limit must not roll over between the counted requests.
    freeze_time(datetime(2026, 11, 5, 17, 30, 10, tzinfo=UTC))
    v = visitor(app)
    _start(v)
    answers = [v.post("/api/v1/public/example-cycle-works/chat/messages", json={"body": f"m{i}"}) for i in range(11)]
    assert [a.status_code for a in answers[:10]] == [201] * 10 and answers[10].status_code == 429
    # F7: Retry-After is what is left of the 60 s window (the clock stands at :10), whole seconds, same as the body.
    assert answers[10].headers["Retry-After"] == "50" and answers[10].json()["retry_after_seconds"] == 50
    starts = [_start(visitor(app)) for _ in range(5)]
    assert starts[-1].status_code == 429  # 5 new chats per hour per IP (the first one above counted too)
    assert starts[-1].headers["Retry-After"] == str(60 * 60 - (30 * 60 + 10))  # 17:30:10 into the 17:00 window


def test_public_chat_can_be_disabled(app: Any, staff: Any) -> None:
    owner = staff(["owner"])
    s = owner.get("/api/v1/org/settings").json()
    owner.patch(
        "/api/v1/org/settings",
        json={"version": s["version"], "settings": {**s["settings"], "public_chat_enabled": False}},
    )
    assert _start(visitor(app)).json()["code"] == "conversations.public_chat_disabled"


def test_send_reply_enqueues_outbox_in_same_transaction_and_tracks_delivery(
    app: Any, staff: Any, registry: Registry, owner_engine: Engine
) -> None:
    conv_id = _start(visitor(app)).json()["conversation_id"]
    msg = (
        staff()
        .post(f"/api/v1/conversations/{conv_id}/messages", json={"direction": "outbound", "body": "See you Tuesday"})
        .json()
    )
    assert msg["delivery_status"] == "pending"
    with owner_engine.connect() as conn:
        assert conn.execute(text("select channel, status from outbox_messages")).one() == ("web_chat", "pending")
    jobs.run_pending(registry)
    events.deliver_pending(registry)
    thread = staff().get(f"/api/v1/conversations/{conv_id}").json()
    assert thread["status"] == "waiting_on_customer" and thread["messages"][-1]["delivery_status"] == "delivered"


def test_link_customer_writes_timeline(app: Any, staff: Any) -> None:
    conv = _start(visitor(app)).json()
    owner = staff(["owner"])
    ada = owner.post("/api/v1/crm/customers", json={"display_name": "Ada Example"}).json()
    thread = owner.get(f"/api/v1/conversations/{conv['conversation_id']}").json()
    linked = owner.patch(
        f"/api/v1/conversations/{conv['conversation_id']}",
        json={"version": thread["version"], "customer_id": ada["id"]},
    )
    assert linked.status_code == 200 and linked.json()["customer_name"] == "Ada Example"
    timeline = owner.get(f"/api/v1/crm/customers/{ada['id']}/timeline").json()
    assert timeline[0]["kind"] == "message" and "Tuesday morning" in timeline[0]["summary"]


def test_staff_logged_call_and_send_reply_executor(app: Any, staff: Any, registry: Registry) -> None:
    operator, owner = staff(), staff(["owner"])
    conv = operator.post(
        "/api/v1/conversations",
        json={"channel": "phone_log", "body": "Dana called about brakes", "visitor_name": "Dana"},
    ).json()
    assert conv["messages"][0]["author_kind"] == "staff" and conv["status"] == "waiting_on_staff"
    p = operator.post(
        "/api/v1/approvals/proposals",
        json={
            "action_type": "conversations.send_reply",
            "title": "Reply to Dana",
            "payload": {"conversation_id": conv["id"], "body": "Tuesday 11:45?"},
        },
    ).json()
    rev = p["current_revision"]
    owner.post(
        f"/api/v1/approvals/proposals/{p['id']}/decisions",
        json={"decision": "approve", "revision_id": rev["id"], "payload_hash": rev["payload_hash"]},
    )
    jobs.run_pending(registry)
    thread = owner.get(f"/api/v1/conversations/{conv['id']}").json()
    assert thread["messages"][-1]["body"] == "Tuesday 11:45?" and thread["messages"][-1]["proposal_id"] == p["id"]
    assert staff(["inventory_manager"]).get("/api/v1/conversations").status_code == 403
    other = staff(["owner"], make_business("example-garden-store"))
    assert other.get(f"/api/v1/conversations/{conv['id']}").status_code == 404


CHAT = "/api/v1/public/example-cycle-works/chat"


def test_disabled_public_chat_blocks_existing_visitor_messages(app: Any, staff: Any) -> None:
    v = visitor(app)
    _start(v)
    owner = staff(["owner"])
    settings = owner.get("/api/v1/org/settings").json()
    owner.patch(
        "/api/v1/org/settings",
        json={"version": settings["version"], "settings": {**settings["settings"], "public_chat_enabled": False}},
    )
    assert v.post(CHAT + "/messages", json={"body": "Blocked"}).status_code == 403
    assert len(v.get(CHAT).json()["messages"]) == 1


def test_public_history_is_bounded_incremental_and_read_limited(
    app: Any, staff: Any, owner_engine: Engine, freeze_time: Any
) -> None:
    from datetime import UTC, datetime

    from app.platform.ids import new_id

    # One fixed instant: the rate-limit window must not roll over while the 70 reads below are counted.
    freeze_time(datetime(2026, 11, 5, 17, 30, 10, tzinfo=UTC))
    v = visitor(app)
    cid = _start(v).json()["conversation_id"]
    # Equal timestamps exercise the id tie-breaker; internal notes must never leak.
    with owner_engine.begin() as conn:
        business = conn.execute(text("select business_id from conversations where id=:c"), {"c": cid}).scalar_one()
        created = conn.execute(
            text("select created_at from messages where conversation_id=:c limit 1"), {"c": cid}
        ).scalar_one()
        conn.execute(
            text(
                "insert into messages(id,business_id,conversation_id,direction,author_kind,body,created_at) "
                "values (:i,:b,:c,:d,'staff',:body,:created)"
            ),
            [
                {
                    "i": new_id(),
                    "created": created,
                    "b": business,
                    "c": cid,
                    "d": "internal" if i == 60 else "outbound",
                    "body": f"history {i}",
                }
                for i in range(121)
            ],
        )
    first = v.get(CHAT).json()
    assert len(first["messages"]) == 50
    assert first["next_cursor"]
    second = v.get(CHAT, params={"cursor": first["next_cursor"]}).json()
    third = v.get(CHAT, params={"cursor": second["next_cursor"]}).json()
    combined = first["messages"] + second["messages"] + third["messages"]
    assert len(combined) == 121 and len({m["id"] for m in combined}) == 121
    assert "history 60" not in {m["body"] for m in combined}
    assert third["next_cursor"]
    assert v.get(CHAT, params={"cursor": third["next_cursor"]}).json()["messages"] == []
    response = v.post(CHAT + "/messages", params={"cursor": third["next_cursor"]}, json={"body": "bounded"})
    assert response.status_code == 201 and [m["body"] for m in response.json()["messages"]] == ["bounded"]
    codes = [v.get(CHAT, params={"cursor": third["next_cursor"]}).status_code for _ in range(70)]
    assert 200 in codes and 429 in codes


@pytest.mark.parametrize("linked", [False, True])
def test_erasure_revokes_visitor_access_and_rejects_stale_writes(
    app: Any, staff: Any, biz: uuid.UUID, linked: bool
) -> None:
    from app.modules.conversations import service
    from app.modules.conversations.repository import get_conversation
    from app.modules.org.testing import system_ctx
    from app.platform.db import transaction
    from app.platform.errors import Conflict

    v = visitor(app)
    cid = uuid.UUID(_start(v).json()["conversation_id"])
    owner = staff(["owner"])
    person = owner.post(
        "/api/v1/crm/customers",
        json={"display_name": "Ada Example", "contact_points": [{"type": "email", "value": "ada@example.com"}]},
    ).json()
    if linked:
        thread = owner.get(f"/api/v1/conversations/{cid}").json()
        response = owner.patch(
            f"/api/v1/conversations/{cid}", json={"version": thread["version"], "customer_id": person["id"]}
        )
        assert response.status_code == 200, response.text
    result = owner.post("/api/v1/admin/erasures", json={"customer_id": person["id"], "confirm_name": "Ada Example"})
    assert result.status_code == 200, result.text
    assert v.get(CHAT).status_code == 403
    assert v.post(CHAT + "/messages", json={"body": "New identifying data"}).status_code == 403
    ctx = system_ctx(biz)
    with transaction(ctx) as db:
        stale = get_conversation(db, ctx, cid)
        with pytest.raises(Conflict):
            service.visitor_message(db, ctx, stale, "Stale authorized request")
    assert (
        owner.post("/api/v1/admin/erasures", json={"customer_id": person["id"], "confirm_name": "anything"}).status_code
        == 200
    )
    assert [m["body"] for m in owner.get(f"/api/v1/conversations/{cid}").json()["messages"]] == ["[erased]"]


def test_delivery_projection_uses_current_outbox_status(
    app: Any, staff: Any, biz: uuid.UUID, owner_engine: Engine
) -> None:
    from app.modules.conversations import service
    from app.modules.org.testing import system_ctx
    from app.platform.db import transaction
    from app.platform.ids import new_id

    cid = _start(visitor(app)).json()["conversation_id"]
    reply = (
        staff().post(f"/api/v1/conversations/{cid}/messages", json={"direction": "outbound", "body": "Reply"}).json()
    )
    with owner_engine.begin() as conn:
        outbox = conn.execute(text("update outbox_messages set status='delivered' returning id")).scalar_one()
    payload = {
        "related_type": "message",
        "related_id": reply["id"],
        "outbox_message_id": str(outbox),
        "status": "unknown",
    }
    ctx = system_ctx(biz)
    with transaction(ctx) as db:
        service.on_delivery_status(db, ctx, payload, new_id())
    assert staff().get(f"/api/v1/conversations/{cid}").json()["messages"][-1]["delivery_status"] == "delivered"


@pytest.mark.parametrize("operation", ["reply", "visitor", "link"])
def test_conversation_mutations_lock_customer_before_thread(
    app: Any, staff: Any, biz: uuid.UUID, owner_engine: Engine, operation: str
) -> None:
    import threading

    from sqlalchemy import event

    from app.modules.conversations import service
    from app.modules.conversations.repository import get_conversation
    from app.modules.org.testing import system_ctx
    from app.platform.db import get_engine, transaction

    owner = staff(["owner"])
    customer = owner.post("/api/v1/crm/customers", json={"display_name": "Ada Example"}).json()["id"]
    cid = owner.post(
        "/api/v1/conversations", json={"channel": "phone_log", "customer_id": customer, "body": "Initial"}
    ).json()["id"]
    entered = threading.Event()
    errors: list[Exception] = []

    def before_execute(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        if threading.current_thread().name == "conversation-write" and (
            ("customers" in statement and ("FOR UPDATE" in statement or statement.startswith("UPDATE")))
            or "pg_advisory_xact_lock" in statement
        ):
            entered.set()

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before_execute)
    ctx = system_ctx(biz, frozenset({"conversations.thread.write"}))

    def mutate() -> None:
        try:
            with transaction(ctx) as db:
                if operation == "reply":
                    service.send_reply(db, ctx, uuid.UUID(cid), "Reply")
                elif operation == "visitor":
                    service.visitor_message(db, ctx, get_conversation(db, ctx, uuid.UUID(cid)), "Visitor reply")
                else:
                    service.link_customer(db, ctx, uuid.UUID(cid), uuid.UUID(customer))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=mutate, name="conversation-write")
    try:
        with owner_engine.begin() as holder:
            holder.execute(text("select id from customers where id=:c for update"), {"c": customer})
            thread.start()
            assert entered.wait(5), "mutation never attempted customer synchronization"
            holder.execute(text("select id from conversations where id=:c for update nowait"), {"c": cid})
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
    finally:
        thread.join(5)
        event.remove(engine, "before_cursor_execute", before_execute)


def test_incremental_history_includes_message_from_earlier_transaction(app: Any, staff: Any, biz: uuid.UUID) -> None:
    from app.modules.conversations import service
    from app.modules.conversations.repository import get_conversation
    from app.modules.org.testing import system_ctx
    from app.platform.db import transaction

    v = visitor(app)
    cid = _start(v).json()["conversation_id"]
    ctx = system_ctx(biz)
    with transaction(ctx) as db:
        db.execute(text("select now()"))  # This transaction starts before the other writer.
        assert (
            staff()
            .post(f"/api/v1/conversations/{cid}/messages", json={"direction": "outbound", "body": "Earlier commit"})
            .status_code
            == 201
        )
        cursor = v.get(CHAT).json()["next_cursor"]
        service.visitor_message(db, ctx, get_conversation(db, ctx, uuid.UUID(cid)), "Later commit")
    result = v.get(CHAT, params={"cursor": cursor}).json()
    assert [m["body"] for m in result["messages"]] == ["Later commit"]


def test_delivery_projection_locks_message_before_outbox(
    app: Any, staff: Any, biz: uuid.UUID, owner_engine: Engine
) -> None:
    import threading

    from sqlalchemy import event

    from app.modules.conversations import service
    from app.modules.org.testing import system_ctx
    from app.platform.db import get_engine, transaction
    from app.platform.ids import new_id

    cid = _start(visitor(app)).json()["conversation_id"]
    reply = (
        staff().post(f"/api/v1/conversations/{cid}/messages", json={"direction": "outbound", "body": "Reply"}).json()
    )
    with owner_engine.connect() as conn:
        outbox = conn.execute(
            text("select outbox_message_id from messages where id=:i"), {"i": reply["id"]}
        ).scalar_one()
    entered = threading.Event()
    errors: list[Exception] = []
    ctx = system_ctx(biz)

    def before_execute(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        if (
            threading.current_thread().name == "delivery-projection"
            and "messages" in statement
            and ("FOR UPDATE" in statement or statement.startswith("UPDATE"))
        ):
            entered.set()

    def project() -> None:
        try:
            with transaction(ctx) as db:
                service.on_delivery_status(
                    db,
                    ctx,
                    {
                        "related_type": "message",
                        "related_id": reply["id"],
                        "outbox_message_id": str(outbox),
                        "status": "unknown",
                    },
                    new_id(),
                )
        except Exception as exc:
            errors.append(exc)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", before_execute)
    thread = threading.Thread(target=project, name="delivery-projection")
    try:
        with owner_engine.begin() as holder:
            holder.execute(text("select id from messages where id=:i for update"), {"i": reply["id"]})
            thread.start()
            assert entered.wait(5)
            holder.execute(text("select id from outbox_messages where id=:i for update nowait"), {"i": outbox})
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
    finally:
        thread.join(5)
        event.remove(engine, "before_cursor_execute", before_execute)
