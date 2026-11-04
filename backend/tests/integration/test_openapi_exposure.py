"""Security re-scan F5: the API description (every route, staff routes included) is not served in production.

`make gen-api` and the contract test read it with `create_app().openapi()` / `python -m app.cli openapi`, never over
HTTP, so they work in every environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.cli import main
from app.main import create_app
from app.platform.config import get_settings

pytestmark = pytest.mark.integration
OPENAPI_FILE = Path(__file__).resolve().parents[2] / "openapi.json"
DESCRIPTION_PATHS = ["/api/v1/openapi.json", "/api/v1/docs", "/api/v1/docs/oauth2-redirect", "/api/v1/redoc"]


@pytest.mark.parametrize("path", DESCRIPTION_PATHS)
def test_api_description_and_docs_are_not_served_in_production(
    registry: Any, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    client = TestClient(create_app(registry))
    assert client.get(path).status_code == 404
    assert client.get("/api/v1/healthz").status_code == 200  # the API itself is there


@pytest.mark.parametrize("app_env", ["dev", "test"])
def test_api_description_and_docs_are_served_for_developers(
    registry: Any, monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    monkeypatch.setattr(get_settings(), "app_env", app_env)
    client = TestClient(create_app(registry))
    description = client.get("/api/v1/openapi.json")
    assert description.status_code == 200 and len(description.json()["paths"]) > 100
    docs = client.get("/api/v1/docs")
    assert docs.status_code == 200 and "swagger" in docs.text.lower()


def test_the_generated_contract_does_not_depend_on_the_environment(
    registry: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`python -m app.cli openapi` is what `make gen-api` runs; it must give the committed file, whatever APP_ENV is."""
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    main(["openapi"])
    assert json.loads(capsys.readouterr().out) == json.loads(OPENAPI_FILE.read_text(encoding="utf-8"))
    assert create_app(registry).openapi() == json.loads(json.dumps(create_app(registry).openapi()))
