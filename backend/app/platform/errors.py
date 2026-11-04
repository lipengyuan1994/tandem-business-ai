"""Domain errors and RFC 9457 problem-details responses.

Services raise these; the exception handlers installed by `install_error_handlers` turn them into
`application/problem+json` responses with a stable `code` (see 04-repository-and-conventions.md).
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.platform.logging import redact_request_path

PROBLEM_BASE = "https://smb.local/problems/"
log = structlog.get_logger(__name__)


class DomainError(Exception):
    status: int = 400
    title: str = "Bad request"

    def __init__(self, code: str, detail: str | None = None, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.extra = extra

    def headers(self) -> dict[str, str]:
        """HTTP headers that belong to this error (for example `Retry-After`); none by default."""
        return {}


class NotFound(DomainError):
    status = 404
    title = "Not found"


class Conflict(DomainError):
    status = 409
    title = "Conflict"


class Forbidden(DomainError):
    status = 403
    title = "Forbidden"


class Unauthenticated(DomainError):
    status = 401
    title = "Authentication required"


class Invalid(DomainError):
    status = 422
    title = "Validation failed"


class RateLimited(DomainError):
    """The caller used up a fixed window of `platform.ratelimit`; `retry_after_seconds` is what is left of it.

    Every 429 carries it twice: as `retry_after_seconds` in the problem body and as the standard `Retry-After` header
    (whole seconds, RFC 9110), so clients and proxies that only read headers also know when to come back.
    """

    status = 429
    title = "Too many requests"

    def __init__(self, code: str, detail: str | None = None, *, retry_after_seconds: int, **extra: Any) -> None:
        super().__init__(code, detail, retry_after_seconds=retry_after_seconds, **extra)
        self.retry_after_seconds = retry_after_seconds

    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(max(1, int(self.retry_after_seconds)))}


def problem_body(
    *, status: int, title: str, code: str, request_id: str | None, detail: str | None = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + code,
        "title": title,
        "status": status,
        "code": code,
        "request_id": request_id,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return body


def _problem(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json", headers=headers)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


_LOCATIONS = ("body", "query", "path", "header", "cookie")
# Field names of the public JSON API are lower snake_case (`lead_time_days`); a few parameters use hyphens.
_PLAIN_NAME = re.compile(r"[a-z][a-z0-9_-]*")


def _staff_field(loc: tuple[Any, ...]) -> str:
    """Dotted path of the failing input (`lines.0.unit_price_minor`), exactly as Pydantic reports it."""
    return ".".join(str(p) for p in loc if p not in ("body", "query", "path"))


def _public_field(loc: tuple[Any, ...]) -> str:
    """The same path for a visitor: JSON field names and list positions, nothing from our code.

    Pydantic puts the name of every union member into `loc` (`DeclineQuote`, `function-after[unique_lines(),
    PublicQuoteInput]`): class and function names that mean nothing to a supplier's form and say more about the code
    than a stranger needs. Only the leading location is dropped, so a field that is itself called `body` stays.
    """
    parts = loc[1:] if loc and loc[0] in _LOCATIONS else loc
    return ".".join(str(p) for p in parts if isinstance(p, int) or _PLAIN_NAME.fullmatch(p))


def install_error_handlers(app: FastAPI, *, public_prefix: str | None = None) -> None:
    """`public_prefix`: requests under this path (the unauthenticated `/api/v1/public/`) get plain field paths in
    validation errors; staff routes keep Pydantic's full paths, which help the people building against them."""

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        body = problem_body(
            status=exc.status,
            title=exc.title,
            code=exc.code,
            request_id=_request_id(request),
            detail=exc.detail,
            **exc.extra,
        )
        return _problem(exc.status, body, exc.headers())

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        public = public_prefix is not None and request.url.path.startswith(public_prefix)
        errors: list[dict[str, Any]] = []
        for e in exc.errors():
            error = {
                "field": (_public_field if public else _staff_field)(tuple(e.get("loc", ()))),
                "code": e.get("type"),
            }
            # Each union member reports its own errors; once their names are gone the same entry can appear twice.
            if not (public and error in errors):
                errors.append(error)
        body = problem_body(
            status=422,
            title="Validation failed",
            code="platform.validation_error",
            request_id=_request_id(request),
            errors=errors,
        )
        return _problem(422, body)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=redact_request_path(request.url.path))
        body = problem_body(
            status=500, title="Internal error", code="platform.internal_error", request_id=_request_id(request)
        )
        return _problem(500, body)


_DESCRIPTIONS = {
    400: "Bad request",
    401: "Authentication required",
    403: "Permission denied",
    404: "Not found (also returned for records of another business)",
    409: "Conflict (version mismatch or business rule)",
    422: "Validation failed",
    429: "Too many requests",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entry for problem+json errors: `responses=error_responses(403, 404, 409)`."""
    return {s: {"description": _DESCRIPTIONS[s], "content": {"application/problem+json": {}}} for s in statuses}
