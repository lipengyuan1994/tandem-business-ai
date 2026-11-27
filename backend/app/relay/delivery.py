"""Forwarding Stripe events to the Tandem business that owns the connected account.

Each event is stored once (by Stripe event id) when Stripe sends it, then posted to the client's events URL,
signed with the client's secret. A refused or failed post is retried with back-off for three days, like Stripe's own
retries. Destinations must be HTTPS on public addresses, except hosts named in RELAY_PRIVATE_CALLBACK_HOSTS.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.parse
from collections.abc import Callable

import httpx
import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import signing
from .config import RelaySettings
from .crypto import UnsealError, unseal
from .models import RelayClient, RelayEvent

log = structlog.get_logger(__name__)
BACKOFF_SECONDS = (60, 300, 1800, 7200, 21600, 86400)
GIVE_UP_AFTER_SECONDS = 3 * 86400
BATCH = 50

Resolver = Callable[[str], list[str]]


class BadDestination(ValueError):
    pass


def resolve(host: str) -> list[str]:
    try:
        return list(dict.fromkeys(str(info[4][0]) for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)))
    except socket.gaierror as exc:
        raise BadDestination(f"{host} does not resolve") from exc


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def check_url(url: str, settings: RelaySettings, *, resolver: Resolver | None = None) -> list[str]:
    """Refuse destinations that could reach the relay's own network. With a resolver, check every address too, and
    return them so the post connects only to these (empty for an operator's private host, which is not resolved)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise BadDestination("the URL has no host")
    private = host in settings.private_hosts
    if parts.scheme != "https" and not (private and parts.scheme == "http"):
        raise BadDestination("the URL must use https")
    if parts.username or parts.password:
        raise BadDestination("the URL must not carry credentials")
    if _is_ip(host):
        raise BadDestination("the URL must name a host, not an IP address")
    if resolver is None or private:
        return []
    addresses = resolver(host)
    if not addresses or not all(_public(ip) for ip in addresses):
        raise BadDestination(f"{host} resolves to a non-public address")
    return addresses


def post_pinned(
    http: httpx.Client, url: str, addresses: list[str], *, content: bytes, headers: dict[str, str]
) -> httpx.Response:
    """Post to one of the addresses checked a moment ago, never to a fresh DNS answer: a name that changes what it
    points at between the check and the post (DNS rebinding) cannot reach the relay's own network. The Host header
    and TLS server name keep the real name, so the certificate is still checked against it. The connection is not
    kept, so it is never reused for another client's name on the same address."""
    if not addresses:
        return http.post(url, content=content, headers=headers)
    target = httpx.URL(url)
    pinned_headers = {**headers, "Host": target.netloc.decode("ascii"), "Connection": "close"}
    for index, address in enumerate(addresses):
        try:
            return http.post(
                target.copy_with(host=address),
                content=content,
                headers=pinned_headers,
                extensions={"sni_hostname": target.host},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if index == len(addresses) - 1:
                raise
    raise AssertionError("checked addresses must not be empty")  # pragma: no cover


def envelope(event: dict[str, object], business_ref: str) -> str:
    """What Tandem receives: enough to know what changed, never the Stripe object itself."""
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    obj = obj if isinstance(obj, dict) else {}
    return json.dumps(
        {
            "id": event.get("id"),
            "type": event.get("type"),
            "account": event.get("account"),
            "business_ref": business_ref,
            "created": event.get("created"),
            "livemode": bool(event.get("livemode")),
            "object": {"id": obj.get("id"), "object": obj.get("object")},
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def deliver_one(
    db: Session,
    row: RelayEvent,
    client: RelayClient,
    http: httpx.Client,
    settings: RelaySettings,
    now: int,
    resolver: Resolver,
) -> bool:
    row.attempts += 1
    error: str | None = None
    try:
        addresses = check_url(client.events_url, settings, resolver=resolver)
        secret = unseal(settings.secret_key.get_secret_value(), client.secret_sealed)
        body = row.body.encode()
        response = post_pinned(
            http,
            client.events_url,
            addresses,
            content=body,
            headers={
                "Content-Type": "application/json",
                signing.CLIENT_HEADER: client.id,
                signing.SIGNATURE_HEADER: signing.sign(secret, now, body),
            },
        )
        if 200 <= response.status_code < 300:
            row.delivered_at, row.next_attempt_at, row.last_error = now, None, None
            return True
        error = f"answered {response.status_code}"
    except (BadDestination, UnsealError) as exc:
        error = str(exc)
    except httpx.HTTPError as exc:
        error = f"could not be reached ({type(exc).__name__})"
    row.last_error = error[:300]
    if now - row.received_at >= GIVE_UP_AFTER_SECONDS:
        row.given_up_at, row.next_attempt_at = now, None
        log.warning("relay.event_given_up", event_id=row.id, client=client.id, error=row.last_error)
    else:
        row.next_attempt_at = now + BACKOFF_SECONDS[min(row.attempts - 1, len(BACKOFF_SECONDS) - 1)]
    return False


def deliver_due(db: Session, http: httpx.Client, settings: RelaySettings, now: int, resolver: Resolver) -> int:
    """Post every event that is due; returns how many were delivered."""
    rows = db.scalars(
        select(RelayEvent)
        .where(
            RelayEvent.delivered_at.is_(None),
            RelayEvent.given_up_at.is_(None),
            or_(RelayEvent.next_attempt_at.is_(None), RelayEvent.next_attempt_at <= now),
        )
        .order_by(RelayEvent.received_at, RelayEvent.id)
        .limit(BATCH)
    ).all()
    delivered = 0
    for row in rows:
        client = db.get(RelayClient, row.client_id)
        if client is None or client.disabled_at is not None:
            row.given_up_at, row.last_error = now, "the client is disabled"
            continue
        delivered += deliver_one(db, row, client, http, settings, now, resolver)
    return delivered
