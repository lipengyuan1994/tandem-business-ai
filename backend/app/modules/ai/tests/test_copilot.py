"""AI-10: real HTTP/DB/tool integration with scripted models only."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from app.main import create_app
from app.modules.ai.gateway.fake import FakeGateway, ScriptFileAdapter
from app.modules.ai.gateway.router import Router, reset_router_cache, set_router_for_tests
from app.modules.ai.gateway.routes import ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.models import AiRun, AssistantMessage, AssistantThread
from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool, system_ctx
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.config import get_settings
from app.platform.db import get_engine, transaction
from app.platform.registry import Registry, set_registry
from app.platform.testing.auth import client_for, ctx_for

BASE = "/api/v1/ai/copilot/threads"


@pytest.fixture(autouse=True)
def clean_router():
    set_router_for_tests(None)
    reset_router_cache()
    yield
    set_router_for_tests(None)
    reset_router_cache()


def new_thread(client: TestClient, title: str = "Stock questions") -> str:
    response = client.post(BASE, json={"title": title})
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"id", "title", "created_at", "updated_at"}
    return str(response.json()["id"])


def send(client: TestClient, thread: str, content: str = "What is low on stock?") -> list[dict[str, Any]]:
    with client.stream("POST", f"{BASE}/{thread}/messages", json={"content": content}) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-accel-buffering"] == "no"
        events = []
        kind = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            if line.startswith("data: "):
                event = json.loads(line.removeprefix("data: "))
                assert event["kind"] == kind
                assert set(event) == {
                    "kind",
                    "run_id",
                    "tool",
                    "status_text",
                    "proposal_id",
                    "proposal_title",
                    "text",
                    "error_code",
                }
                events.append(event)
        return events


def test_sse_stream_emits_step_proposal_final_in_order(registry: Registry, owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    set_registry(registry)  # seed builds its own registry; HTTP authentication must use this test's resolver
    gw = FakeGateway(
        [
            step_tool("list_low_stock"),
            step_tool("propose_reorder", item_ids=[demo.items["Cable set"]], note="Restock cables."),
            step_final("Proposed a reorder for review."),
        ]
    )
    set_router_for_tests(fake_router_by_class(agent=gw))
    client = client_for(create_app(registry), registry, demo.business, AI_SYSTEM_PERMISSIONS)
    events = send(client, new_thread(client), "Draft a reorder for low stock.")
    assert [e["kind"] for e in events] == ["step", "step", "proposal", "final"]
    assert events[0]["status_text"] == "Checking low stock…"
    assert events[1]["status_text"] == "Drafting a reorder proposal…"
    assert events[2]["proposal_title"] == "Reorder Cable set ×5"
    assert uuid.UUID(events[2]["proposal_id"])
    assert events[-1]["text"] == "Proposed a reorder for review."
    assert len({e["run_id"] for e in events}) == 1
    assert '<untrusted source="tool_result"' in gw.calls[1].messages[-1].content


def test_sse_error_event_on_budget_exceeded(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> None:
    biz = make_business()
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", 0)
    gw = FakeGateway([])
    set_router_for_tests(fake_router_by_class(agent=gw))
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use"})
    tid = new_thread(client)
    [event] = send(client, tid)
    assert event["kind"] == "error" and event["error_code"] == "ai.budget_exceeded"
    assert gw.calls == []
    with transaction(system_ctx(biz)) as db:
        row = db.get(AiRun, uuid.UUID(event["run_id"]))
        assert row is not None and row.status == "budget_exceeded"
    assert [m["role"] for m in client.get(f"{BASE}/{tid}").json()["messages"]] == ["user"]


def test_copilot_tools_limited_by_user_permissions(registry: Registry) -> None:
    biz = make_business()
    gw = FakeGateway([step_tool("list_appointments", start="2026-11-05", end="2026-11-06"), step_final()])
    set_router_for_tests(fake_router_by_class(agent=gw))
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use", "catalog.item.read"})
    events = send(client, new_thread(client))
    assert events[-1]["kind"] == "final"
    system = gw.calls[0].messages[0].content
    assert '"list_services"' in system and '"list_appointments"' not in system
    assert '"propose_payment_reminder"' not in system and '"propose_quote_request"' not in system
    assert '"error":"ai.tool_not_allowed"' in gw.calls[1].messages[-1].content


def test_thread_owner_only(registry: Registry) -> None:
    biz = make_business()
    app = create_app(registry)
    owner = client_for(app, registry, biz, {"ai.assistant.use"})
    tid = new_thread(owner)
    stranger = client_for(app, registry, biz, {"ai.assistant.use"})
    outsider = client_for(app, registry, make_business(slug="other"), {"ai.assistant.use"})
    for client in (stranger, outsider):
        for response in (
            client.get(f"{BASE}/{tid}"),
            client.post(f"{BASE}/{tid}/messages", json={"content": "hello"}),
        ):
            assert response.status_code == 404
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["code"] == "ai.thread_not_found"
        assert client.get(BASE).json() == []


def test_rate_limit_30_per_hour(registry: Registry, freeze_time: Any) -> None:
    freeze_time(datetime(2026, 11, 5, 17, 30, tzinfo=UTC))
    biz = make_business()
    gw = FakeGateway(lambda _: step_final())
    set_router_for_tests(fake_router_by_class(agent=gw))
    app = create_app(registry)
    client = client_for(app, registry, biz, {"ai.assistant.use"})
    tid = new_thread(client)
    for _ in range(30):
        assert send(client, tid)[-1]["kind"] == "final"
    # A fresh thread cannot bypass the user-wide limit.
    other_tid = new_thread(client)
    response = client.post(f"{BASE}/{other_tid}/messages", json={"content": "hello"})
    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "platform.rate_limited"
    assert response.headers["retry-after"] == "1800"
    assert client.get(f"{BASE}/{other_tid}").json()["messages"] == []
    other_user = client_for(app, registry, biz, {"ai.assistant.use"})
    assert send(other_user, new_thread(other_user))[-1]["kind"] == "final"
    freeze_time(datetime(2026, 11, 5, 18, 0, tzinfo=UTC))
    assert send(client, tid)[-1]["kind"] == "final"
    assert len(gw.calls) == 32


def test_messages_persisted_with_run_and_proposals(registry: Registry, owner_engine: Engine) -> None:
    demo = seed_demo(owner_engine)
    set_registry(registry)
    gw = FakeGateway(
        [
            step_tool("list_low_stock"),
            step_tool("propose_reorder", item_ids=[demo.items["Cable set"]]),
            # The saved IDs must be actual effects, regardless of what the model claims in its final.
            step_final("Proposed.", proposals=[uuid.uuid4()]),
        ]
    )
    set_router_for_tests(fake_router_by_class(agent=gw))
    client = client_for(create_app(registry), registry, demo.business, AI_SYSTEM_PERMISSIONS)
    tid = new_thread(client)
    events = send(client, tid, "Draft a reorder.")
    detail = client.get(f"{BASE}/{tid}").json()
    assert set(detail) == {"id", "title", "created_at", "updated_at", "messages"}
    user, assistant = detail["messages"]
    assert set(user) == {"id", "role", "content", "run_id", "proposal_ids", "proposals", "created_at"}
    assert (user["role"], user["content"], user["run_id"], user["proposal_ids"]) == (
        "user",
        "Draft a reorder.",
        None,
        [],
    )
    assert (assistant["role"], assistant["content"], assistant["run_id"], assistant["proposal_ids"]) == (
        "assistant",
        "Proposed.",
        events[-1]["run_id"],
        [events[-2]["proposal_id"]],
    )
    with owner_engine.connect() as conn:
        run = conn.execute(
            text("select on_behalf_of_user_id from ai_runs where id=:r"), {"r": assistant["run_id"]}
        ).scalar_one()
        thread_user = conn.execute(text("select user_id from assistant_threads where id=:t"), {"t": tid}).scalar_one()
    assert run == thread_user


def test_list_threads_own_only_newest_first(registry: Registry) -> None:
    biz = make_business()
    app = create_app(registry)
    client = client_for(app, registry, biz, {"ai.assistant.use"})
    ids = [new_thread(client, f"Chat {i}") for i in range(52)]
    new_thread(client_for(app, registry, biz, {"ai.assistant.use"}), "Private")
    rows = client.get(BASE).json()
    assert [r["id"] for r in rows] == list(reversed(ids[-50:]))


def test_retention_job_deletes_old_messages(registry: Registry) -> None:
    biz, other = make_business(), make_business(slug="other")
    now = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)
    clock.set_clock(lambda: now)
    retained: list[uuid.UUID] = []
    for business in (biz, other):
        with transaction(system_ctx(business)) as db:
            old = AssistantThread(business_id=business, user_id=uuid.uuid4(), title="Old")
            mixed = AssistantThread(business_id=business, user_id=uuid.uuid4(), title="Mixed")
            empty = AssistantThread(business_id=business, user_id=uuid.uuid4(), title="Empty")
            db.add_all([old, mixed, empty])
            db.flush()
            for thread, age in ((old, 91), (mixed, 91), (mixed, 90)):
                db.add(
                    AssistantMessage(
                        business_id=business,
                        thread_id=thread.id,
                        role="user",
                        content="Question",
                        created_at=now - timedelta(days=age),
                    )
                )
            if business == biz:
                retained.append(mixed.id)
    job = registry.jobs["ai.assistant_retention"]
    assert job.transactional is False
    job.handler(system_ctx(biz), {})  # type: ignore[call-arg, arg-type]
    cron = next(c for c in registry.cron if c.name == "ai-assistant-retention")
    assert (cron.job_kind, cron.every_seconds, cron.per_business) == ("ai.assistant_retention", 86400, True)
    with transaction(system_ctx(biz)) as db:
        assert list(db.scalars(select(AssistantThread.id))) == retained
        assert len(list(db.scalars(select(AssistantMessage)))) == 1
    with transaction(system_ctx(other)) as db:
        assert len(list(db.scalars(select(AssistantThread)))) == 3
        assert len(list(db.scalars(select(AssistantMessage)))) == 3


def test_no_db_transaction_held_while_streaming(registry: Registry) -> None:
    biz = make_business()
    pool = get_engine().pool
    baseline = pool.checkedout()  # type: ignore[attr-defined]
    counts = []

    def model(_):
        counts.append(pool.checkedout())  # type: ignore[attr-defined]
        return step_tool("list_services", query="") if len(counts) == 1 else step_final()

    set_router_for_tests(fake_router_by_class(agent=FakeGateway(model)))
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use", "catalog.item.read"})
    assert send(client, new_thread(client))[-1]["kind"] == "final"
    assert counts == [baseline, baseline]


def test_history_includes_last_ten_messages_staff_trusted_answers_fenced(registry: Registry) -> None:
    biz = make_business()
    gw = FakeGateway(lambda _: step_final("Previous answer."))
    set_router_for_tests(fake_router_by_class(agent=gw))
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use"})
    tid = new_thread(client)
    for i in range(6):
        send(client, tid, f"Staff marker-{i}.")
    context = gw.calls[-1].messages[1].content
    assert "Staff marker-0." not in context
    assert all(f"Staff marker-{i}." in context for i in range(1, 6))
    assert context.count("Previous answer.") == 5
    # Staff text is trusted; earlier assistant answers can quote customer text, so they are fenced (AI-14).
    assert context.count("<untrusted") == 5
    assert all("<untrusted" not in line for line in context.splitlines() if "Staff marker-" in line)


@pytest.mark.parametrize("body", [{"content": ""}, {"content": "x" * 4001}, {}])
def test_message_validation_before_stream(registry: Registry, body: dict[str, str]) -> None:
    client = client_for(create_app(registry), registry, make_business(), {"ai.assistant.use"})
    response = client.post(f"{BASE}/{new_thread(client)}/messages", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "platform.validation_error"


def test_thread_default_title_and_title_limit(registry: Registry) -> None:
    client = client_for(create_app(registry), registry, make_business(), {"ai.assistant.use"})
    assert client.post(BASE, json={}).json()["title"]
    assert client.post(BASE, json={"title": "x" * 121}).status_code == 422


def test_all_copilot_endpoints_require_permission(registry: Registry) -> None:
    client = client_for(create_app(registry), registry, make_business(), set())
    for response in (
        client.get(BASE),
        client.post(BASE, json={}),
        client.get(f"{BASE}/{uuid.uuid4()}"),
        client.post(f"{BASE}/{uuid.uuid4()}/messages", json={"content": "hello"}),
    ):
        assert response.status_code == 403


def test_disconnect_finishes_current_step_without_another_model_call(registry: Registry) -> None:
    from app.modules.ai.usecases import copilot

    biz = make_business()
    ctx = ctx_for(biz, {"ai.assistant.use", "catalog.item.read"})
    tid = copilot.create_thread(ctx, copilot.ThreadCreate()).id
    prepared = copilot.prepare_message(ctx, tid, copilot.MessageCreate(content="List services."))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def model(_):
        entered.set()
        assert release.wait(5)
        return step_tool("list_services", query="")

    gw = FakeGateway(model)
    set_router_for_tests(fake_router_by_class(agent=gw))
    stopped = threading.Event()
    events = []

    def worker():
        try:
            copilot.run_message(prepared, events.append, stopped)
        finally:
            finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(5)
        stopped.set()
        release.set()
        assert finished.wait(5)
    finally:
        release.set()
        thread.join(5)
    assert len(gw.calls) == 1
    assert events[-1].kind == "error" and events[-1].error_code == "ai.interrupted"
    with transaction(system_ctx(biz)) as db:
        run = db.scalar(select(AiRun))
        assert run is not None and (run.status, run.steps) == ("failed", 1)


def test_stream_yields_before_worker_finishes_and_close_signals_stop(registry: Registry, monkeypatch) -> None:
    from app.modules.ai.agent.protocol import StepEvent
    from app.modules.ai.usecases import copilot

    stopped_seen = threading.Event()

    def worker(prepared, emit, stopped):
        emit(StepEvent(kind="step", run_id=uuid.uuid4(), tool="list_services", status_text="Checking services…"))
        assert stopped.wait(5)
        stopped_seen.set()

    monkeypatch.setattr(copilot, "run_message", worker)

    async def consume():
        stream = copilot.stream_message(None)  # worker double does not use prepared input
        first = await asyncio.wait_for(anext(stream), timeout=2)
        assert first.startswith("event: step\ndata: ")
        await stream.aclose()
        assert await asyncio.to_thread(stopped_seen.wait, 2)

    asyncio.run(consume())


def test_dev_fake_script_copilot_reply_works_unchanged(registry: Registry) -> None:
    path = Path(__file__).resolve().parents[5] / "deploy/config/ai-fake-script.dev.yaml"
    adapter = ScriptFileAdapter("dev-fake", path)
    set_router_for_tests(Router(
        RoutesFile(providers={"dev-fake": ProviderCfg(kind="fake", vendor="vultr")},
                   routes={"agent": [RouteEntry(provider="dev-fake", model="demo")]}),
        {"dev-fake": adapter},
    ))  # fmt: skip
    client = client_for(create_app(registry), registry, make_business(), {"ai.assistant.use"})
    tid = new_thread(client)
    [event] = send(client, tid)
    assert event["kind"] == "final" and event["text"].startswith("Demo mode:")
    assert client.get(f"{BASE}/{tid}").json()["messages"][-1]["content"] == event["text"]


def test_terminal_event_follows_committed_message_and_emits_without_transaction(registry: Registry) -> None:
    from app.modules.ai.usecases import copilot

    biz = make_business()
    ctx = ctx_for(biz, {"ai.assistant.use", "catalog.item.read"})
    tid = copilot.create_thread(ctx, copilot.ThreadCreate()).id
    prepared = copilot.prepare_message(ctx, tid, copilot.MessageCreate(content="List services."))
    set_router_for_tests(fake_router_by_class(agent=FakeGateway([step_tool("list_services", query=""),
                                                               step_final("No services found.")])))  # fmt: skip
    pool = get_engine().pool
    baseline = pool.checkedout()  # type: ignore[attr-defined]
    events = []

    def emit(event):
        assert pool.checkedout() == baseline  # type: ignore[attr-defined]
        if event.kind == "final":
            with transaction(system_ctx(biz)) as db:
                saved = db.scalar(select(AssistantMessage).where(AssistantMessage.role == "assistant"))
                assert saved is not None and saved.content == event.text and saved.run_id == event.run_id
        events.append(event)

    copilot.run_message(prepared, emit, threading.Event())
    assert [e.kind for e in events] == ["step", "final"]


def test_worker_crash_emits_sanitized_error_and_closes_run(registry: Registry, caplog) -> None:
    marker = "private-staff-text-canary"

    def crash(_):
        raise RuntimeError(marker)

    set_router_for_tests(fake_router_by_class(agent=FakeGateway(crash)))
    biz = make_business()
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use"})
    [event] = send(client, new_thread(client), marker)
    assert event["kind"] == "error" and event["error_code"] == "ai.interrupted"
    assert marker not in json.dumps(event) and marker not in caplog.text
    with transaction(system_ctx(biz)) as db:
        run = db.get(AiRun, uuid.UUID(event["run_id"]))
        assert run is not None and run.status == "failed" and run.error_code == "ai.interrupted"


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_socket_disconnect_signals_worker_even_during_send(registry: Registry, monkeypatch, spec_version) -> None:
    from starlette.requests import ClientDisconnect

    from app.modules.ai.agent.protocol import StepEvent
    from app.modules.ai.api import send_copilot_message
    from app.modules.ai.usecases import copilot

    ctx = ctx_for(make_business(), {"ai.assistant.use"})
    tid = copilot.create_thread(ctx, copilot.ThreadCreate()).id
    stopped_seen = threading.Event()

    def worker(prepared, emit, stopped):
        emit(StepEvent(kind="step", run_id=uuid.uuid4(), tool="list_services"))
        if stopped.wait(5):
            stopped_seen.set()

    monkeypatch.setattr(copilot, "run_message", worker)
    response = send_copilot_message(tid, copilot.MessageCreate(content="Services?"), ctx)

    async def disconnect():
        body_sent = asyncio.Event()

        async def receive():
            await body_sent.wait()
            return {"type": "http.disconnect"}

        async def send_chunk(message):
            if message["type"] == "http.response.body" and message["body"]:
                body_sent.set()
                if spec_version == "2.4":
                    raise OSError("socket closed")
                await asyncio.sleep(0)

        try:
            await response({"type": "http", "asgi": {"spec_version": spec_version}}, receive, send_chunk)
        except ClientDisconnect:
            assert spec_version == "2.4"
        assert await asyncio.to_thread(stopped_seen.wait, 2)

    asyncio.run(disconnect())
