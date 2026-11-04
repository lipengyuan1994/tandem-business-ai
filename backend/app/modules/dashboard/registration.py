from __future__ import annotations

from app.platform.registry import Registry

from . import api


def register(reg: Registry) -> None:
    reg.add_router(api.router, "/dashboard", ["dashboard"])
