"""P1-05: require(), CSRF, registry freezing, health."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, Depends

from app.main import create_app
from app.platform.context import Context
from app.platform.permissions import public_context, require
from app.platform.registry import Registry
from app.platform.testing.auth import client_for


def _hook(reg: Registry) -> None:
    router = APIRouter()

    @router.get("/thing")
    def read_thing(ctx: Context = Depends(require("test.thing.read"))) -> dict[str, Any]:
        return {"business_id": str(ctx.business_id), "user": str(ctx.user_id)}

    @router.post("/thing")
    def write_thing(ctx: Context = Depends(require("test.thing.write"))) -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/public/{business_slug}/hello")
    def hello(ctx: Context = Depends(public_context)) -> dict[str, str]:
        return {"business_id": str(ctx.business_id), "actor": ctx.actor_kind}

    reg.add_router(router, "/test", ["test"])


@pytest.fixture
def app_and_registry(registry_builder: Any) -> tuple[Any, Registry]:
    reg = registry_builder(_hook)
    return create_app(reg), reg


def test_require_returns_context_with_permissions(app_and_registry: Any, business: Any) -> None:
    app, reg = app_and_registry
    client = client_for(app, reg, business, {"test.thing.read"})
    response = client.get("/api/v1/test/thing")
    assert response.status_code == 200
    assert response.json()["business_id"] == str(business)


def test_require_rejects_missing_permission_and_no_session(app_and_registry: Any, business: Any) -> None:
    app, reg = app_and_registry
    assert client_for(app, reg, business, set()).get("/api/v1/test/thing").json()["code"] == "auth.permission_denied"
    from fastapi.testclient import TestClient

    anonymous = TestClient(app).get("/api/v1/test/thing")
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "auth.unauthenticated"


def test_require_rejects_missing_csrf_on_unsafe_methods(app_and_registry: Any, business: Any) -> None:
    app, reg = app_and_registry
    no_csrf = client_for(app, reg, business, {"test.thing.write"}, csrf=False)
    response = no_csrf.post("/api/v1/test/thing")
    assert response.status_code == 403 and response.json()["code"] == "auth.csrf_failed"
    assert client_for(app, reg, business, {"test.thing.write"}).post("/api/v1/test/thing").status_code == 200


def test_require_rejects_session_without_active_business(app_and_registry: Any) -> None:
    app, reg = app_and_registry
    response = client_for(app, reg, None, {"test.thing.read"}).get("/api/v1/test/thing")
    assert response.status_code == 403 and response.json()["code"] == "auth.no_active_business"


def test_public_context_resolves_slug(app_and_registry: Any, business: Any) -> None:
    from fastapi.testclient import TestClient

    app, _ = app_and_registry
    client = TestClient(app)
    ok = client.get("/api/v1/test/public/example-cycle-works/hello").json()
    assert ok == {"business_id": str(business), "actor": "visitor"}
    assert client.get("/api/v1/test/public/nope-nope/hello").status_code == 404


def test_registry_frozen_after_startup(registry: Registry) -> None:
    with pytest.raises(RuntimeError, match="frozen"):
        registry.add_permissions({"late.permission"})


def test_health_endpoints(registry: Registry) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(create_app(registry))
    assert client.get("/api/v1/healthz").json() == {"status": "ok"}
    ready = client.get("/api/v1/readyz")
    assert ready.status_code == 200 and ready.json()["migration"]
