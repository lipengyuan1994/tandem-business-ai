"""Liveness and readiness endpoints (public by design)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.platform.db import get_engine

router = APIRouter()


@router.get("/healthz", summary="Process is up")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Database reachable and migrated", response_model=None)
def readyz() -> dict[str, Any] | JSONResponse:
    try:
        with get_engine().connect() as conn:
            version = conn.execute(text("select version_num from alembic_version")).scalar()
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "unreachable or not migrated"}, status_code=503)
    return {"status": "ok", "migration": version}
