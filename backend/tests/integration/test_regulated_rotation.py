"""Only reauthentication, not business switching, restarts the absolute lifetime."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.modules.admin_ops.tests.test_data import CYCLE, GARDEN, NOW
from app.modules.admin_ops.tests.test_data import demo as demo
from app.modules.identity.testing import add_member, login_as
from app.platform import clock
from app.platform.config import get_settings


@pytest.mark.parametrize("profile", ["standard", "regulated"])
@pytest.mark.parametrize("same_business", [True, False])
def test_rotation_preserves_authentication_time_and_absolute_expiry(
    demo: Any, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch, profile: str, same_business: bool
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", profile)
    owner = demo["owner"]
    add_member(owner.user_id, GARDEN, ["owner"])
    destination = CYCLE if same_business else GARDEN
    for elapsed in (timedelta(hours=7), timedelta(hours=7, minutes=50)):
        at = NOW + elapsed
        clock.set_clock(lambda at=at: at)
        with owner_engine.begin() as conn:
            # Model active use between the time jumps, so this tests the absolute cap, not idle expiry.
            conn.execute(
                text("update sessions set last_seen_at=:at where user_id=:u and revoked_at is null"),
                {"at": at, "u": owner.user_id},
            )
        switched = owner.post("/api/v1/auth/active-business", json={"business_id": str(destination)})
        assert switched.status_code == 200, switched.text
        owner.headers["X-CSRF-Token"] = switched.json()["csrf_token"]
        with owner_engine.connect() as conn:
            row = conn.execute(
                text(
                    "select authenticated_at, created_at, expires_at from sessions "
                    "where user_id=:u and revoked_at is null"
                ),
                {"u": owner.user_id},
            ).one()
        assert row.authenticated_at == NOW
        assert row.created_at == at
        assert row.expires_at == NOW + timedelta(hours=8 if profile == "regulated" else 12)
    clock.set_clock(lambda: NOW + timedelta(hours=8))
    if profile == "regulated":
        assert owner.get("/api/v1/auth/me").status_code == 401
        assert owner.post("/api/v1/auth/active-business", json={"business_id": str(destination)}).status_code == 401
        # A genuine new authentication may start a new absolute lifetime.
        fresh = login_as(demo["app"], destination, ["owner"], user_id=owner.user_id)
        assert fresh.get("/api/v1/auth/me").status_code == 200
    else:
        at = NOW + timedelta(hours=13)
        clock.set_clock(lambda at=at: at)
        with owner_engine.begin() as conn:
            conn.execute(
                text("update sessions set last_seen_at=:at where user_id=:u and revoked_at is null"),
                {"at": at, "u": owner.user_id},
            )
        assert owner.get("/api/v1/auth/me").status_code == 401
