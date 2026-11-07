from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text

from app.modules.org.schemas import BusinessSettings


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("  ", None), ("  Ask about refunds  ", "Ask about refunds"), ("x" * 2000, "x" * 2000)],
)
def test_guardrail_normalization(value: str | None, expected: str | None) -> None:
    assert BusinessSettings(ai_reply_guardrail=value).ai_reply_guardrail == expected


def test_guardrail_length() -> None:
    with pytest.raises(ValidationError):
        BusinessSettings(ai_reply_guardrail="x" * 2001)


def test_guardrail_permission_audit_version_and_tenant(
    client: Any, cycle: UUID, garden: UUID, owner_engine: Engine
) -> None:
    staff = client(cycle)
    current = staff.get("/api/v1/org/settings").json()
    body = {
        "version": current["version"],
        "settings": {**current["settings"], "ai_reply_guardrail": " Refunds need a person "},
    }
    assert staff.patch("/api/v1/org/settings", json=body).status_code == 403
    owner = client(cycle, {"org.settings.read", "org.settings.write", "ai.settings.write"})
    saved = owner.patch("/api/v1/org/settings", json=body)
    assert saved.status_code == 200 and saved.json()["settings"]["ai_reply_guardrail"] == "Refunds need a person"
    assert owner.patch("/api/v1/org/settings", json=body).status_code == 409
    assert client(garden).get("/api/v1/org/settings").json()["settings"]["ai_reply_guardrail"] is None
    with owner_engine.connect() as db:
        changes = db.scalar(
            text("select changes from audit_events where business_id=:b and action='org.settings.updated'"),
            {"b": cycle},
        )
    assert "ai_reply_guardrail" in changes
