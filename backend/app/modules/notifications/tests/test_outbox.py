"""M10 notifications tests (docs/roadmap/modules/M10-notifications.md#tests-must-exist)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.notifications import adapters, service
from app.modules.notifications.templates import render
from app.modules.org import public as org
from app.platform import clock, events, jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Invalid
from app.platform.ids import new_id
from app.platform.registry import Registry


def _business(slug: str = "example-cycle-works") -> uuid.UUID:
    business_id = new_id()
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business_id)
    with transaction(ctx) as db:
        org.create_business(db, ctx, name=slug, slug=slug, timezone="America/Denver", currency="USD", locale="en-US")
    return business_id


@pytest.fixture
def biz() -> uuid.UUID:
    return _business()


@pytest.fixture
def ctx(biz: uuid.UUID) -> Context:
    return Context(request_id="t", actor_kind="system", user_id=None, business_id=biz)


def _enqueue(ctx: Context, key: str = "reply-1", channel: str = "simulated") -> uuid.UUID:
    with transaction(ctx) as db:
        return service.enqueue_message(
            db,
            ctx,
            channel=channel,
            recipient="ada@example.com",
            template_key="test_message",
            variables={"name": "Ada"},
            idempotency_key=key,
            related_type="message",
            related_id=new_id(),
        )


def _row(engine: Engine, outbox_id: uuid.UUID) -> Any:
    with engine.connect() as conn:
        return conn.execute(
            text("select status, attempts, last_error from outbox_messages where id = :i"), {"i": outbox_id}
        ).one()


class Scripted:
    def __init__(self, *results: adapters.Result) -> None:
        self.results, self.calls = list(results), 0

    def send(self, message: adapters.RenderedMessage, idempotency_key: str) -> adapters.Result:
        self.calls += 1
        return self.results.pop(0) if self.results else adapters.Delivered("ok")


def test_delivered_and_status_event(ctx: Context, registry: Registry, owner_engine: Engine) -> None:
    outbox_id = _enqueue(ctx)
    jobs.run_pending(registry)
    assert _row(owner_engine, outbox_id).status == "delivered"
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("select payload->>'status' from domain_events where type='notifications.delivery_status_changed'")
            ).scalar()
            == "delivered"
        )


def test_enqueue_rolls_back_with_business_transaction(ctx: Context, owner_engine: Engine) -> None:
    with pytest.raises(RuntimeError), transaction(ctx) as db:
        service.enqueue_message(
            db,
            ctx,
            channel="simulated",
            recipient=None,
            template_key="test_message",
            variables={"name": "x"},
            idempotency_key="rolled-back",
        )
        raise RuntimeError("business change failed")
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages")).scalar() == 0
        assert conn.execute(text("select count(*) from jobs")).scalar() == 0


def test_idempotency_key_unique_prevents_duplicate_enqueue(ctx: Context, owner_engine: Engine) -> None:
    assert _enqueue(ctx, "same-key") == _enqueue(ctx, "same-key")
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages")).scalar() == 1


def test_retryable_backoff_then_failed(
    ctx: Context, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = Scripted(*[adapters.RetryableError("provider 503")] * 10)
    monkeypatch.setitem(adapters.ADAPTERS, "simulated", flaky)
    monkeypatch.setattr(service, "ADAPTERS", adapters.ADAPTERS)
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    outbox_id = None
    for hour in range(0, 8):
        clock.set_clock(lambda h=hour: start + timedelta(hours=h))
        if outbox_id is None:
            outbox_id = _enqueue(ctx)
        jobs.run_pending(registry)
    assert outbox_id is not None
    row = _row(owner_engine, outbox_id)
    assert row.status == "failed" and row.attempts == service.MAX_ATTEMPTS and flaky.calls == service.MAX_ATTEMPTS


def test_unknown_not_retried_and_resolution_audited(
    app_client: Any, ctx: Context, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeout = Scripted(adapters.Unknown("timeout after sending"))
    monkeypatch.setitem(adapters.ADAPTERS, "simulated", timeout)
    outbox_id = _enqueue(ctx)
    jobs.run_pending(registry)
    jobs.run_pending(registry)
    assert _row(owner_engine, outbox_id).status == "unknown" and timeout.calls == 1
    operator = app_client(["operator"])
    resolved = operator.post(f"/api/v1/notifications/outbox/{outbox_id}/resolve", json={"resolution": "retry"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "pending"
    jobs.run_pending(registry)
    assert _row(owner_engine, outbox_id).status == "delivered" and timeout.calls == 2
    again = operator.post(f"/api/v1/notifications/outbox/{outbox_id}/resolve", json={"resolution": "delivered"})
    assert again.status_code == 409
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("select count(*) from audit_events where action like 'notifications.outbox.resolved_%'")
            ).scalar()
            == 1
        )


def test_stuck_sending_becomes_unknown(
    ctx: Context, registry: Registry, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Dies:
        def send(self, message: Any, key: str) -> adapters.Result:
            raise SystemExit("worker killed during the external call")

    monkeypatch.setitem(adapters.ADAPTERS, "simulated", Dies())
    start = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    clock.set_clock(lambda: start)
    outbox_id = _enqueue(ctx)
    with pytest.raises(SystemExit):
        service.deliver(ctx.business_id, outbox_id)  # type: ignore[arg-type]
    assert _row(owner_engine, outbox_id).status == "sending"
    clock.set_clock(lambda: start + timedelta(minutes=10))
    with transaction(ctx) as db:
        service.job_reap_sending(db, ctx, {})
    assert _row(owner_engine, outbox_id).status == "unknown"


def test_template_sandbox_blocks_attribute_access() -> None:
    with pytest.raises(Invalid):
        render("test_message", {})  # missing variable is an error, not a silent blank
    _subject, body = render("test_message", {"name": "{{ 7*7 }}"})
    assert body == "Hello {{ 7*7 }}"  # variables are data, never re-rendered
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    from app.modules.notifications import templates

    assert isinstance(templates._ENV, ImmutableSandboxedEnvironment)
    with pytest.raises(Exception):  # noqa: B017 - sandbox raises SecurityError
        templates._ENV.from_string("{{ x.__class__.__mro__ }}").render(x="s")
    with pytest.raises(Invalid):
        render("no_such_template", {})


def test_approvers_notified_on_proposal_created(app_client: Any, biz: uuid.UUID, registry: Registry) -> None:
    creator = app_client(["operator"])
    admin, stock = app_client(["admin"]), app_client(["inventory_manager"])
    body = {"action_type": "testing.noop_write", "title": "Add test item", "payload": {"name": "x"}}
    assert creator.post("/api/v1/approvals/proposals", json=body).status_code == 201
    events.deliver_pending(registry)
    events.deliver_pending(registry)  # at-least-once delivery must not duplicate notifications
    mine = admin.get("/api/v1/notifications").json()
    assert mine["unread"] == 1 and mine["items"][0]["link"].startswith("/approvals/")
    assert stock.get("/api/v1/notifications").json()["unread"] == 0  # cannot approve this type
    assert admin.post("/api/v1/notifications/read-all").json()["updated"] == 1
    assert admin.get("/api/v1/notifications").json()["unread"] == 0


@pytest.fixture
def app_client(registry: Registry, biz: uuid.UUID) -> Any:
    app = create_app(registry)
    return lambda roles: login_as(app, biz, roles)
