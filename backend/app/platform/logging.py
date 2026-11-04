"""Structured JSON logging with request IDs and redaction (ADR-017).

Never log message bodies, emails, phone numbers, prompts or secrets. The redaction processor is a
safety net for mistakes, not permission to log sensitive fields.
"""

from __future__ import annotations

import logging
import re
import traceback
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from structlog.types import ExcInfo

SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "body",
    "email",
    "phone",
    "prompt",
    "api_key",
}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
CAPABILITY_PATH_RE = re.compile(r"(/(?:api/v1/public/quotes|q)/)[^/\s?#]+")


def redact_request_path(path: str) -> str:
    """Mask capability segments regardless of their encoding, validity or route registration."""
    return CAPABILITY_PATH_RE.sub(r"\1[redacted]", path)


def _redact(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event):
        if any(s in key.lower() for s in SENSITIVE_KEYS):
            event[key] = "[redacted]"
        elif isinstance(event[key], str):
            event[key] = redact_request_path(EMAIL_RE.sub("[email]", event[key]))
    return event


def _format_exception(exc_info: ExcInfo) -> str:
    # Driver messages and chained exceptions can contain values even with hide_parameters=True.
    pending = [exc_info[1]]
    seen: set[int] = set()
    while pending:
        exc = pending.pop()
        if id(exc) in seen:
            continue
        seen.add(id(exc))
        if isinstance(exc, SQLAlchemyError):
            # Keep locations and exception type, without messages, SQL, parameters or source lines.
            frames = "".join(
                f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}\n'
                for frame in traceback.extract_tb(exc_info[2])
            )
            return f"{frames}{exc_info[0].__name__}: [database exception details redacted]"
        pending.extend(e for e in (exc.__cause__, exc.__context__) if e is not None)
        if isinstance(exc, BaseExceptionGroup):
            pending.extend(exc.exceptions)
    return "".join(traceback.format_exception(*exc_info)).rstrip()


def configure_logging(level: str = "info", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", level=level.upper())
    # httpx logs every outbound call as `HTTP Request: GET <full url>`. Keycloak lookups carry a person's e-mail address
    # in the query string, and the redaction below never sees standard-library records, so keep these lines out.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.ExceptionRenderer(_format_exception),
            _redact,
            structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID (or accepts a safe incoming one) and binds it to the log context."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if re.fullmatch(r"[A-Za-z0-9-]{8,64}", incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, path=redact_request_path(request.url.path), method=request.method
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
