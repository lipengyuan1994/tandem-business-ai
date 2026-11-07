"""Public disclosure reflects only this business's live reply rule and pause switch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.approvals.models import AutonomyRule
from app.modules.identity.testing import make_user
from app.modules.org.testing import make_business
from app.platform.context import system_context
from app.platform.db import transaction
from app.platform.registry import Registry


@pytest.mark.parametrize(
    "level,shadow,active,paused,expected",
    [
        (None, False, True, False, False),
        ("act_notify", True, True, False, False),
        ("act_notify", False, True, False, True),
        ("act", False, True, False, True),
        ("act_notify", False, True, True, False),
        ("act_notify", False, False, False, False),
        ("ask", False, True, False, False),
        ("draft", False, True, False, False),
        ("off", False, True, False, False),
    ],
)
def test_public_profile_auto_reply_flag(
    registry: Registry,
    owner_engine: Engine,
    level: str | None,
    shadow: bool,
    active: bool,
    paused: bool,
    expected: bool,
) -> None:
    business = make_business()
    other = make_business(slug="example-garden-store")
    grantor = make_user()
    with transaction(system_context(other)) as db:
        # Another business's live rule must never make this business's flag true.
        db.add(
            AutonomyRule(
                business_id=other,
                action_type="conversations.send_reply",
                level="act_notify",
                shadow=False,
                active=True,
                granted_by=grantor,
            )
        )
    if level:
        with transaction(system_context(business)) as db:
            db.add(
                AutonomyRule(
                    business_id=business,
                    action_type="conversations.send_reply",
                    level=level,
                    shadow=shadow,
                    active=active,
                    granted_by=grantor,
                )
            )
    if paused:
        with owner_engine.begin() as db:
            db.execute(
                text(
                    "update business_settings set settings=jsonb_set(settings,'{ai_autonomy_paused}','true') "
                    "where business_id=:b"
                ),
                {"b": business},
            )
    visitor = TestClient(create_app(registry), base_url="https://testserver")
    result = visitor.get("/api/v1/public/example-cycle-works/profile")
    assert result.status_code == 200
    assert result.json()["ai_auto_replies"] is expected
    assert visitor.get("/api/v1/public/example-garden-store/profile").json()["ai_auto_replies"] is True
