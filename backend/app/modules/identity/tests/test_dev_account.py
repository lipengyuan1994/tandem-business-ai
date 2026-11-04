"""dev-account CLI support: fresh Keycloak login for a seeded staff user (dev and test only)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.identity import keycloak_admin, service
from app.modules.identity.testing import make_user
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction

SYSTEM = Context(request_id="t", actor_kind="system", user_id=None, business_id=None)


@pytest.fixture
def fake_keycloak(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"deleted": []}
    monkeypatch.setattr(keycloak_admin, "find_user_id", lambda email: "kc-old")
    monkeypatch.setattr(keycloak_admin, "delete_user", lambda uid: calls["deleted"].append(uid))
    monkeypatch.setattr(keycloak_admin, "create_user", lambda email, name, password: "kc-new")
    monkeypatch.setattr(keycloak_admin, "temporary_password", lambda: "Temp-test-value")
    return calls


def test_dev_account_recreates_keycloak_user_and_relinks(fake_keycloak: dict[str, Any], owner_engine: Any) -> None:
    from sqlalchemy import text

    make_user("casey@example.com")
    with transaction(SYSTEM) as db:
        assert service.dev_reset_account(db, "Casey@example.com") == "Temp-test-value"
    assert fake_keycloak["deleted"] == ["kc-old"]
    with owner_engine.connect() as conn:
        subject = conn.execute(text("select oidc_subject from users where email = 'casey@example.com'")).scalar()
    assert subject == "kc-new"


def test_dev_account_is_disabled_in_production(fake_keycloak: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    make_user("casey@example.com")
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    with transaction(SYSTEM) as db, pytest.raises(PermissionError):
        service.dev_reset_account(db, "casey@example.com")
    assert fake_keycloak["deleted"] == []
