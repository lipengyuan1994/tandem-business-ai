"""Every route has a permission dependency or is explicitly public."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from app.main import build_registry, create_app
from app.platform.permissions import permission_of


def _calls(dependant: Any) -> list[Any]:
    found = []
    for dep in dependant.dependencies:
        found.append(dep.call)
        found += _calls(dep)
    return found


def unprotected_routes(app: FastAPI, public: set[str]) -> list[str]:
    """Walks every route, including those inside included routers (FastAPI >= 0.16x wraps them lazily)."""
    bad = []
    seen = 0
    for route_ctx in iter_route_contexts(app.routes):
        route = route_ctx.original_route
        if not isinstance(route, APIRoute):
            continue
        seen += 1
        if route_ctx.path not in public and not any(permission_of(c) for c in _calls(route.dependant)):
            bad.append(f"{sorted(route_ctx.methods or [])} {route_ctx.path}")
    assert seen > 0, "no API routes found: the checker cannot see the routes"
    return bad


def test_every_route_has_a_permission() -> None:
    reg = build_registry()
    assert unprotected_routes(create_app(reg), reg.public_routes) == []


def test_checker_sees_platform_routes_and_their_full_paths() -> None:
    reg = build_registry()
    paths = {c.path for c in iter_route_contexts(create_app(reg).routes) if isinstance(c.original_route, APIRoute)}
    assert {"/api/v1/healthz", "/api/v1/readyz"} <= paths


def test_checker_catches_unprotected_route() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/naked")
    def naked() -> dict[str, str]:
        return {}

    app.include_router(router)
    assert unprotected_routes(app, set()) == ["['GET'] /naked"]
