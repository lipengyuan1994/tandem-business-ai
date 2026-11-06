from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.conversations import public as conversations
from app.modules.org.testing import make_business
from app.platform import clock, retention
from app.platform.context import Context, system_context
from app.platform.db import transaction

from .test_knowledge import ctx as ctx

NOW = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)


def conversation(ctx: Context) -> Any:
    with transaction(ctx) as db:
        return conversations.start_conversation(
            db, ctx, channel="web_chat", body="A policy question?", author_kind="visitor"
        )


@pytest.mark.parametrize("days", [30, 730])
def test_retention_uses_configured_window_and_preserves_open_recent_other_business(
    ctx: Context,
    owner_engine: Engine,
    registry: Any,
    days: int,
) -> None:
    clock.set_clock(lambda: NOW)
    other = system_context(make_business(slug="retention-other"), frozenset({"conversations.thread.read"}))
    conv, foreign_conv = conversation(ctx), conversation(other)
    cutoff = NOW - timedelta(days=days)
    with owner_engine.begin() as db:
        db.execute(
            text(
                "update business_settings set "
                "settings=jsonb_set(settings,'{retention_days_conversations}',cast(:days as jsonb)) "
                "where business_id=:b"
            ),
            {"b": ctx.business_id, "days": str(days)},
        )
        for status, updated, label, business, conversation_id in [
            ("open", cutoff - timedelta(days=1), "open", ctx.business_id, conv),
            ("dismissed", cutoff - timedelta(seconds=1), "expired-dismissed", ctx.business_id, conv),
            ("done", cutoff - timedelta(seconds=1), "expired-done", ctx.business_id, conv),
            ("dismissed", cutoff, "boundary", ctx.business_id, conv),
            ("done", cutoff + timedelta(seconds=1), "recent", ctx.business_id, conv),
            ("dismissed", NOW, "recently-closed", ctx.business_id, conv),
            ("done", cutoff - timedelta(days=1), "foreign", other.business_id, foreign_conv),
        ]:
            db.execute(
                text(
                    "insert into kb_suggestions "
                    "(id,business_id,conversation_id,question_text,status,created_at,updated_at) "
                    "values (gen_random_uuid(),:b,:c,:q,:s,:created,:updated)"
                ),
                {
                    "b": business,
                    "c": conversation_id,
                    "q": label,
                    "s": status,
                    "created": cutoff - timedelta(days=10),
                    "updated": updated,
                },
            )
    spec = registry.jobs["knowledge.retention"]
    assert spec.transactional
    cron = next(c for c in registry.cron if c.name == "knowledge-retention")
    assert (cron.job_kind, cron.every_seconds, cron.per_business) == ("knowledge.retention", 86400, True)
    with transaction(system_context(ctx.business_id)) as db:
        spec.handler(db, system_context(ctx.business_id), {})
    with owner_engine.connect() as db:
        assert set(db.scalars(text("select question_text from kb_suggestions"))) == {
            "open",
            "boundary",
            "recent",
            "recently-closed",
            "foreign",
        }


def test_retention_is_bounded_and_continuation_keeps_business(
    ctx: Context, owner_engine: Engine, registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock.set_clock(lambda: NOW)
    monkeypatch.setattr(retention, "BATCH", 2)
    conv = conversation(ctx)
    with owner_engine.begin() as db:
        for label in ("first", "second", "third", "fourth"):
            db.execute(
                text(
                    "insert into kb_suggestions (id,business_id,conversation_id,question_text,status,updated_at) "
                    "values (gen_random_uuid(),:b,:c,:q,'done',:old)"
                ),
                {"b": ctx.business_id, "c": conv, "q": label, "old": NOW - timedelta(days=731)},
            )
    spec = registry.jobs["knowledge.retention"]
    with transaction(system_context(ctx.business_id)) as db:
        spec.handler(db, system_context(ctx.business_id), {})
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from kb_suggestions")) == 2
        job = db.execute(text("select business_id,run_at,priority from jobs where kind='knowledge.retention'")).one()
        assert tuple(job) == (ctx.business_id, NOW + timedelta(minutes=1), 200)
    with transaction(system_context(ctx.business_id)) as db:
        spec.handler(db, system_context(ctx.business_id), {})
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from kb_suggestions")) == 0
        assert db.scalar(text("select count(*) from jobs where kind='knowledge.retention'")) == 1


def test_retention_requires_a_business(ctx: Context, registry: Any) -> None:
    with transaction(ctx) as db, pytest.raises(ValueError, match="requires a business"):
        registry.jobs["knowledge.retention"].handler(db, system_context(None), {})
