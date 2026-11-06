"""Offline projection checks for the widget's system-message discriminator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.conversations import api


@pytest.mark.parametrize("author,is_system", [("system", True), ("staff", False), ("assistant", False)])
def test_public_thread_identifies_system_messages_without_exposing_staff_metadata(
    monkeypatch: Any, author: str, is_system: bool
) -> None:
    created = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    published = datetime(2026, 11, 5, 17, 2, tzinfo=UTC)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        author_kind=author,
        direction="outbound",
        body="Example reply",
        created_at=created,
        published_at=published,
    )
    monkeypatch.setattr(api.service, "public_messages", lambda *args, **kwargs: ([row], "publication-cursor"))
    monkeypatch.setattr(api.org, "get_business_brief", lambda *args: SimpleNamespace(name="Example Cycle Works"))
    result = api._public_thread(None, None, SimpleNamespace(id=uuid.uuid4()), "previous")  # type: ignore[arg-type]
    message = result.model_dump(mode="json")["messages"][0]
    assert message["is_system"] is is_system
    assert message["from_business"] is True
    assert message["created_at"] == "2026-11-05T17:02:00Z"
    assert "author_kind" not in message and "author_user_id" not in message
    assert result.next_cursor == "publication-cursor"
