"""Database exception parameters and driver details must never enter rendered logs."""

from __future__ import annotations

import io
import json

import pytest
import structlog
from sqlalchemy.exc import DataError

from app.platform.db import get_engine
from app.platform.logging import configure_logging


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
def test_database_exception_logging_omits_parameters_and_driver_details(wrapped: bool, json_output: bool) -> None:
    output = io.StringIO()
    previous = structlog.get_config().copy()
    verifier, message = "synthetic-pkce-value-938", "synthetic-private-chat-761"
    try:
        structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=output))
        configure_logging(json_output=json_output)
        try:
            try:
                raise DataError(
                    "insert into synthetic_table values (:code_verifier, :body)",
                    {"code_verifier": verifier, "body": message},
                    ValueError(f"driver detail: {message}"),
                )
            except DataError as exc:
                if wrapped:
                    raise RuntimeError("outer failure") from exc
                raise
        except Exception:
            structlog.get_logger("exception-regression").exception("request_failed")
        rendered = output.getvalue()
        assert "request_failed" in rendered
        assert "DataError" in rendered or "RuntimeError" in rendered
        assert verifier not in rendered and message not in rendered
        assert "parameters:" not in rendered
    finally:
        structlog.configure(**previous)


def test_rendered_exception_is_redacted_after_formatting() -> None:
    output = io.StringIO()
    previous = structlog.get_config().copy()
    try:
        structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=output))
        configure_logging()
        try:
            raise ValueError("synthetic.person@example.com")
        except ValueError:
            structlog.get_logger("redaction-regression").exception("request_failed")
        rendered = json.loads(output.getvalue())
        assert "synthetic.person@example.com" not in rendered["exception"]
        assert "[email]" in rendered["exception"]
    finally:
        structlog.configure(**previous)


def test_database_engine_hides_bound_parameters() -> None:
    assert get_engine().hide_parameters is True


@pytest.mark.parametrize("route", ["/api/v1/public/quotes/{token}", "/q/{token}"])
@pytest.mark.parametrize("json_output", [False, True])
def test_capability_route_errors_mask_paths_before_logging(
    route: str, json_output: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.platform import errors
    from app.platform.errors import install_error_handlers
    from app.platform.logging import RequestIdMiddleware

    output = io.StringIO()
    previous = structlog.get_config().copy()
    token = "synthetic-capability.value-761"
    seen: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []

    def observe(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(event))
        return event

    try:
        structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=output))
        configure_logging(json_output=json_output)
        processors = structlog.get_config()["processors"]
        structlog.configure(processors=[processors[0], observe, *processors[1:]])
        monkeypatch.setattr(errors, "log", structlog.get_logger("capability-error-regression"))
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)
        install_error_handlers(app)

        @app.get(route)
        def fail(token: str) -> None:
            contexts.append(structlog.contextvars.get_contextvars())
            structlog.get_logger("capability-regression").info("before_failure")
            raise RuntimeError("synthetic endpoint failure")

        response = TestClient(app, raise_server_exceptions=False).get(route.replace("{token}", token))
        assert response.status_code == 500
        safe_path = route.replace("{token}", "[redacted]")
        assert contexts[0]["path"] == safe_path
        assert {e["event"] for e in seen} == {"before_failure", "unhandled_error"}
        assert all(e["path"] == safe_path for e in seen)
        assert token not in output.getvalue()
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.configure(**previous)


def test_outbound_http_requests_are_not_logged_with_their_urls(caplog: pytest.LogCaptureFixture) -> None:
    """httpx logs `HTTP Request: GET <url>` at INFO. Keycloak lookups carry a person's e-mail address in the query
    string, and structlog's redaction never sees standard-library records, so the line must not be written."""
    import logging

    import httpx
    import respx

    previous = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    address = "synthetic.person%40example.com"
    try:
        configure_logging()
        caplog.set_level(logging.INFO)  # as in the api process, where the root logger is at INFO
        with respx.mock:
            respx.get("http://keycloak.test/admin/users").respond(200, json=[])
            httpx.get(f"http://keycloak.test/admin/users?email={address}&exact=true")
        assert [r for r in caplog.records if r.name in ("httpx", "httpcore")] == []
        assert address not in caplog.text and "synthetic.person@example.com" not in caplog.text
    finally:
        for name, level in previous.items():
            logging.getLogger(name).setLevel(level)
