"""Concurrent turns, retries and cancellation with PostgreSQL and FakeGateway."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

import pytest

from app.main import create_app
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.tests.helpers import fake_router_by_class, step_final
from app.modules.ai.tests.test_copilot import BASE, new_thread
from app.modules.ai.usecases import copilot
from app.modules.org.testing import make_business
from app.platform.testing.auth import client_for, ctx_for


@pytest.fixture(autouse=True)
def clean_router():
    reset_router_cache()
    yield
    reset_router_cache()


def test_copilot_rejects_concurrent_turn_before_inserting_message(registry):
    client = client_for(create_app(registry), registry, make_business(), {"ai.assistant.use"})
    tid = new_thread(client)
    entered, release = threading.Event(), threading.Event()

    def answer(_):
        entered.set()
        assert release.wait(5)
        return step_final("First answer.")

    set_router_for_tests(fake_router_by_class(agent=FakeGateway(answer)))
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(client.post, f"{BASE}/{tid}/messages", json={"content": "First question."})
        try:
            assert entered.wait(5)
            second = client.post(f"{BASE}/{tid}/messages", json={"content": "Second question."})
            assert second.status_code == 409
            assert second.headers["content-type"] == "application/problem+json"
            assert second.json()["code"] == "ai.thread_busy"
        finally:
            release.set()
        assert first.result().status_code == 200
    set_router_for_tests(fake_router_by_class(agent=FakeGateway([step_final("Second answer.")])))
    assert client.post(f"{BASE}/{tid}/messages", json={"content": "Second question."}).status_code == 200
    assert [m["content"] for m in client.get(f"{BASE}/{tid}").json()["messages"]] == [
        "First question.",
        "First answer.",
        "Second question.",
        "Second answer.",
    ]


def test_copilot_key_replays_same_message_and_rejects_changed_body(registry):
    client = client_for(create_app(registry), registry, make_business(), {"ai.assistant.use"})
    gateway = FakeGateway([step_final("Saved answer.")])
    set_router_for_tests(fake_router_by_class(agent=gateway))
    tid = new_thread(client)
    path = f"{BASE}/{tid}/messages"
    headers = {"Idempotency-Key": "turn-1"}
    first = client.post(path, headers=headers, json={"content": "Hello."})
    retry = client.post(path, headers=headers, json={"content": "Hello."})
    assert first.status_code == retry.status_code == 200
    assert retry.text == first.text
    assert len(gateway.calls) == 1
    assert len(client.get(f"{BASE}/{tid}").json()["messages"]) == 2
    changed = client.post(path, headers=headers, json={"content": "Changed."})
    assert changed.status_code == 409 and changed.json()["code"] == "ai.idempotency_key_reused"
    assert client.post(path, headers={"Idempotency-Key": "k" * 65}, json={"content": "Hello."}).status_code == 422


def test_process_generation_capacity_fails_before_streaming(registry, monkeypatch):
    client = client_for(create_app(registry), registry, make_business(), {"ai.assistant.use"})
    monkeypatch.setattr(copilot, "_GENERATION_SLOTS", threading.BoundedSemaphore(0))
    tid = new_thread(client)
    response = client.post(f"{BASE}/{tid}/messages", json={"content": "Hello."})
    assert response.status_code == 429
    assert response.json()["code"] == "ai.copilot_busy"
    assert not client.get(f"{BASE}/{tid}").json()["messages"]


def test_cancelled_worker_entry_does_no_initialization(registry, monkeypatch):
    ctx = ctx_for(make_business(), {"ai.assistant.use"})
    tid = copilot.create_thread(ctx, copilot.ThreadCreate()).id
    prepared = copilot.prepare_message(ctx, tid, copilot.MessageCreate(content="Hello."))
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(copilot, "load_router", lambda: pytest.fail("cancelled worker initialized"))
    copilot.run_message(prepared, lambda e: pytest.fail("cancelled worker emitted"), stopped)


def test_disconnected_stream_cancels_queued_future(monkeypatch):
    async def consume():
        loop = asyncio.get_running_loop()
        queued = loop.create_future()
        monkeypatch.setattr(loop, "run_in_executor", lambda *a: queued)
        stream = copilot.stream_message(None)
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert queued.cancelled()

    asyncio.run(consume())


def test_summary_key_replays_existing_proposal_after_lost_response(registry, owner_engine, monkeypatch):
    from sqlalchemy import func, select

    from app.modules.ai.models import AiEffect
    from app.modules.ai.tests.test_summary import PERMISSIONS, SUMMARY, thread
    from app.modules.ai.usecases import summary
    from app.platform.db import transaction

    business = make_business()
    conversation = thread(business)
    ctx = ctx_for(business, PERMISSIONS)
    gateway = FakeGateway([SUMMARY.model_dump_json()])
    router = fake_router_by_class(fast_extract=gateway)
    execute = summary.execute_tool

    def lose_response(*args, **kwargs):
        execute(*args, **kwargs)  # the proposal and effect have committed
        raise RuntimeError("response lost")

    monkeypatch.setattr(summary, "execute_tool", lose_response)
    with pytest.raises(RuntimeError, match="response lost"):
        summary.run(ctx, conversation, router=router, idempotency_key="summary-1")
    monkeypatch.setattr(summary, "execute_tool", execute)
    retried = summary.run(ctx, conversation, router=router, idempotency_key="summary-1")
    assert summary.run(ctx, conversation, router=router, idempotency_key="summary-1") == retried
    with transaction(ctx) as db:
        assert db.scalar(select(func.count()).select_from(AiEffect)) == 1
    assert len(gateway.calls) == 1


def test_summary_http_idempotency_header(registry):
    from app.modules.ai.tests.test_summary import PERMISSIONS, SUMMARY, thread

    business = make_business()
    path = f"/api/v1/ai/conversations/{thread(business)}/summary"
    client = client_for(create_app(registry), registry, business, PERMISSIONS)
    gateway = FakeGateway([SUMMARY.model_dump_json()])
    set_router_for_tests(fake_router_by_class(fast_extract=gateway))
    response = client.post(path, headers={"Idempotency-Key": "x"})
    assert response.status_code == 200, response.text
    assert client.post(path, headers={"Idempotency-Key": "x"}).json() == response.json()
    assert client.post(path, headers={"Idempotency-Key": "x" * 65}).status_code == 422
    assert len(gateway.calls) == 1


def test_copilot_message_order_survives_clock_rollback(registry):
    from datetime import timedelta

    from app.platform import clock

    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use"})
    tid = copilot.create_thread(ctx, copilot.ThreadCreate()).id
    original_time = clock.now()
    gateway = FakeGateway([step_final("First answer."), step_final("Second answer.")])
    set_router_for_tests(fake_router_by_class(agent=gateway))
    prepared = copilot.prepare_message(ctx, tid, copilot.MessageCreate(content="First question."))
    clock.set_clock(lambda: original_time - timedelta(hours=1))
    copilot.run_message(prepared, lambda _: None, threading.Event())
    prepared = copilot.prepare_message(ctx, tid, copilot.MessageCreate(content="Second question."))
    copilot.run_message(prepared, lambda _: None, threading.Event())
    messages = copilot.get_thread(ctx, tid).messages
    assert [message.content for message in messages] == [
        "First question.",
        "First answer.",
        "Second question.",
        "Second answer.",
    ]
    assert all(a.created_at < b.created_at for a, b in pairwise(messages))
