"""Builds the relay client from settings. Tests replace `http_client` to route calls to the relay test app."""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable

import httpx

from app.platform import http
from app.platform.config import get_settings

from .relay import RelayApi

EVENTS_PATH = "/api/v1/payments/relay/events"


def _default_client() -> httpx.Client:
    settings = get_settings()
    host = urllib.parse.urlsplit(settings.payments_relay_url).hostname or ""
    if settings.payments_relay_private:
        return http.client(local_hosts=[host])
    return http.client()


http_client: Callable[[], httpx.Client] = _default_client


def configured() -> bool:
    return bool(get_settings().payments_relay_url)


def relay_url() -> str:
    return get_settings().payments_relay_url.rstrip("/")


def events_url() -> str:
    settings = get_settings()
    return settings.payments_events_url or settings.app_base_url.rstrip("/") + EVENTS_PATH


def api(client: httpx.Client) -> RelayApi:
    return RelayApi(client, relay_url())


def app_base_url() -> str:
    return get_settings().app_base_url.rstrip("/")


def enrollment_token() -> str:
    return get_settings().payments_relay_enrollment_token.get_secret_value()


def bank_transfers() -> bool:
    return get_settings().payments_bank_transfers


PAY_PATH = "/pay/"


def pay_url(token: str) -> str:
    return f"{app_base_url()}{PAY_PATH}{token}"
