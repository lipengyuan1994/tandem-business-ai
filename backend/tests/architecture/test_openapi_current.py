"""backend/openapi.json must match the app (run `make gen-api` after API changes)."""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

OPENAPI = Path(__file__).resolve().parents[2] / "openapi.json"


def test_openapi_file_is_current() -> None:
    assert OPENAPI.exists(), "run `make gen-api`"
    assert json.loads(OPENAPI.read_text(encoding="utf-8")) == json.loads(
        json.dumps(create_app().openapi(), sort_keys=True)
    )
