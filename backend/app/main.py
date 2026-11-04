"""HTTP composition root: the only place (with app/worker.py) that knows every module."""

from __future__ import annotations

import importlib
from types import ModuleType

from fastapi import FastAPI

from app.modules.dependencies import MODULES
from app.platform.config import get_settings
from app.platform.errors import install_error_handlers
from app.platform.health import router as health_router
from app.platform.logging import RequestIdMiddleware, configure_logging
from app.platform.registry import Registry, set_registry

API_PREFIX = "/api/v1"


def installed_modules() -> list[ModuleType]:
    """Import every module package that exists, in dependency order."""
    found = []
    for name in MODULES:
        try:
            found.append(importlib.import_module(f"app.modules.{name}"))
        except ModuleNotFoundError as exc:
            if exc.name != f"app.modules.{name}":
                raise
    return found


def load_models() -> None:
    """Import every module's models so SQLAlchemy metadata is complete (Alembic, tests)."""
    import app.platform.models  # noqa: F401

    for name in MODULES:
        try:
            importlib.import_module(f"app.modules.{name}.models")
        except ModuleNotFoundError as exc:
            if exc.name not in (f"app.modules.{name}", f"app.modules.{name}.models"):
                raise


def build_registry(extra: list[ModuleType] | None = None) -> Registry:
    reg = Registry()
    reg.add_public_route(API_PREFIX + "/healthz")
    reg.add_public_route(API_PREFIX + "/readyz")
    for module in [*installed_modules(), *(extra or [])]:
        module.register(reg)
    reg.freeze()
    set_registry(reg)
    return reg


def create_app(registry: Registry | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "dev")
    reg = registry or build_registry()
    # The API description lists every route, staff routes included (130 paths). It is for developers: production does
    # not serve it or the docs page (security re-scan F5). `app.openapi()` does not depend on the URL, so
    # `python -m app.cli openapi` (make gen-api) and the contract test work in every environment.
    developer_docs = not settings.is_prod
    app = FastAPI(
        title="SMB Operations Suite API",
        version="0.1.0",
        openapi_url=API_PREFIX + "/openapi.json" if developer_docs else None,
        docs_url=API_PREFIX + "/docs" if developer_docs else None,
        redoc_url=None,
    )
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app, public_prefix=API_PREFIX + "/public/")
    app.include_router(health_router, prefix=API_PREFIX, tags=["platform"])
    for router, prefix, tags in reg.routers:
        app.include_router(router, prefix=API_PREFIX + prefix, tags=list(tags))
    app.state.registry = reg
    return app
