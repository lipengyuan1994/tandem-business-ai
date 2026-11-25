"""Builds the QuickBooks clients from settings. Tests replace `http_client` to route calls to a mock."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.platform import http
from app.platform.config import get_settings
from app.platform.errors import Conflict

from .quickbooks import QuickBooksApi, QuickBooksAuth

CALLBACK_PATH = "/api/v1/accounting/quickbooks/callback"

http_client: Callable[[], httpx.Client] = lambda: http.client()  # noqa: E731


def configured() -> bool:
    settings = get_settings()
    return bool(settings.quickbooks_client_id and settings.quickbooks_client_secret.get_secret_value())


def environment() -> str:
    return get_settings().quickbooks_environment


def redirect_uri() -> str:
    settings = get_settings()
    return settings.quickbooks_redirect_uri or settings.app_base_url.rstrip("/") + CALLBACK_PATH


def auth(client: httpx.Client) -> QuickBooksAuth:
    if not configured():
        raise Conflict(
            "accounting.not_configured", detail="QUICKBOOKS_CLIENT_ID and QUICKBOOKS_CLIENT_SECRET are unset"
        )
    settings = get_settings()
    return QuickBooksAuth(
        client,
        settings.quickbooks_client_id,
        settings.quickbooks_client_secret.get_secret_value(),
        redirect_uri(),
    )


def api(client: httpx.Client, env: str, realm_id: str, access_token: str) -> QuickBooksApi:
    return QuickBooksApi(client, env, realm_id, access_token)
