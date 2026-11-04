"""The one rule for the outbox channel of a normal e-mail (M10 rule 3a, docs/runbooks/email.md)."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import Engine, text

from app.modules.notifications import public as notifications
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction

SMTP_URL = "smtp://mail.example.com:587?from=no-reply@example.com"


@pytest.mark.parametrize(("smtp_url", "channel"), [("", "simulated"), (SMTP_URL, "email")])
def test_email_channel_is_simulated_until_smtp_is_configured(
    monkeypatch: pytest.MonkeyPatch, smtp_url: str, channel: str
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", smtp_url)
    assert notifications.email_channel() == channel


# ------------------------------------------------------------------------------------------ simulated_delivery()
@pytest.mark.parametrize("smtp_url", ["", SMTP_URL])
def test_simulated_delivery_forces_the_simulated_channel_only_inside_the_block(
    monkeypatch: pytest.MonkeyPatch, smtp_url: str
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", smtp_url)
    before = notifications.email_channel()
    with notifications.simulated_delivery():
        assert notifications.email_channel() == "simulated"
    assert notifications.email_channel() == before == ("email" if smtp_url else "simulated")


def test_simulated_delivery_does_not_leak_when_the_block_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    with pytest.raises(RuntimeError, match="boom"), notifications.simulated_delivery():
        assert notifications.email_channel() == "simulated"
        raise RuntimeError("boom")
    assert notifications.email_channel() == "email"


def test_simulated_delivery_does_not_leak_when_the_block_exits_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """SystemExit (the seed's way to refuse) and KeyboardInterrupt are BaseExceptions: the reset must still run."""
    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    for exit_with in (SystemExit("refused"), KeyboardInterrupt()):
        with pytest.raises(type(exit_with)), notifications.simulated_delivery():
            raise exit_with
        assert notifications.email_channel() == "email"


def test_simulated_delivery_blocks_nest_and_the_inner_one_does_not_end_the_outer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    with notifications.simulated_delivery():
        with notifications.simulated_delivery():
            assert notifications.email_channel() == "simulated"
        assert notifications.email_channel() == "simulated"
    assert notifications.email_channel() == "email"


def test_simulated_delivery_is_not_seen_by_other_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker thread or request handler running at the same time keeps the normal rule."""
    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    seen: list[str] = []
    with notifications.simulated_delivery():
        thread = threading.Thread(target=lambda: seen.append(notifications.email_channel()))
        thread.start()
        thread.join()
        assert notifications.email_channel() == "simulated"
    assert seen == ["email"]


def test_simulated_delivery_queues_simulated_rows_from_the_start_and_leaves_explicit_email_alone(
    monkeypatch: pytest.MonkeyPatch, business: uuid.UUID, owner_engine: Engine
) -> None:
    """Rows are created with the right channel (never rewritten); `channel="email"` asked for by name is honoured."""
    monkeypatch.setattr(get_settings(), "smtp_url", SMTP_URL)
    ctx = Context(request_id="t", actor_kind="system", user_id=None, business_id=business)

    def queue(key: str, channel: str) -> None:
        with transaction(ctx) as db:
            notifications.enqueue_message(
                db,
                ctx,
                channel=channel,
                recipient="ada@example.com",
                template_key="test_message",
                variables={"name": "Ada"},
                idempotency_key=key,
            )

    with notifications.simulated_delivery():
        queue("inside-rule", notifications.email_channel())
        queue("inside-explicit", "email")
    queue("outside-rule", notifications.email_channel())
    with owner_engine.connect() as conn:
        rows = dict(conn.execute(text("select idempotency_key, channel from outbox_messages")).all())
    assert rows == {"inside-rule": "simulated", "inside-explicit": "email", "outside-rule": "email"}
