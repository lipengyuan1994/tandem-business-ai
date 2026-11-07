"""The admin action queues through the worker, with permission, rate limit and audit."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.registry import Registry


@pytest.fixture
def app(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(get_settings(), "smtp_url", "smtp://mail.example.com:587?from=no-reply@example.com")
    return create_app(registry)


@pytest.mark.parametrize(
    "role", ["owner", "admin", "demo_reviewer", "operator", "inventory_manager", "marketing_manager", "auditor"]
)
def test_test_email_permission(app: Any, role: str, owner_engine: Engine) -> None:
    client = login_as(app, make_business(), [role])
    response = client.post("/api/v1/admin/test-email", json={"to": "ada@example.com"})
    assert response.status_code == (202 if role in {"owner", "admin", "demo_reviewer"} else 403)
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages")).scalar_one() == (
            role in {"owner", "admin", "demo_reviewer"}
        )


def test_email_rate_limit_and_audit_and_host_only(
    app: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, freeze_time: Any
) -> None:
    from datetime import UTC, datetime

    # One fixed instant: the hourly window must not roll over between the counted requests.
    freeze_time(datetime(2026, 11, 5, 17, 30, 10, tzinfo=UTC))
    business = make_business()
    client = login_as(app, business, ["owner"])
    monkeypatch.setattr(
        get_settings(), "smtp_url", "smtp://test-user:private-password@mail.example.com:587?from=no-reply@example.com"
    )
    body = client.get("/api/v1/admin/system").json()
    assert body["email"] == {"configured": True, "host": "mail.example.com"}
    assert "private-password" not in str(body) and "test-user" not in str(body)
    for _ in range(5):
        r = client.post("/api/v1/admin/test-email", json={"to": "ada@example.com"})
        assert r.status_code == 202 and r.json()["status"] == "pending"
    refused = client.post("/api/v1/admin/test-email", json={"to": "ada@example.com"})
    assert refused.status_code == 429 and 1 <= int(refused.headers["Retry-After"]) <= 3600  # staff route too (F7)
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "select count(*) from outbox_messages where channel='email' "
                    "and template_key='test_message' and status='pending'"
                )
            ).scalar_one()
            == 5
        )
        audits = conn.execute(
            text("select summary, changes from audit_events where action='admin_ops.test_email.queued'")
        ).all()
        assert len(audits) == 5 and "ada@example.com" not in str(audits)
        assert conn.execute(text("select count(*) from jobs where kind='notifications.deliver'")).scalar_one() == 5
    # The business scope cannot throttle another business.
    other = login_as(app, make_business("example-second-shop"), ["admin"])
    assert other.post("/api/v1/admin/test-email", json={"to": "ada@example.com"}).status_code == 202


def test_email_disabled_and_invalid_recipient(app: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    client = login_as(app, make_business(), ["admin"])
    assert client.post("/api/v1/admin/test-email", json={"to": "not-an-email"}).status_code == 422
    monkeypatch.setattr(get_settings(), "smtp_url", "")
    assert client.get("/api/v1/admin/system").json()["email"] == {"configured": False, "host": None}
    r = client.post("/api/v1/admin/test-email", json={"to": "ada@example.com"})
    assert r.status_code == 422 and r.json()["code"] == "admin_ops.email_not_configured"
    with owner_engine.connect() as conn:
        assert conn.execute(text("select count(*) from outbox_messages")).scalar_one() == 0
