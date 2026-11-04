"""Outbound HTTP with an egress allowlist (06-security-and-privacy.md#egress).

All outbound calls from the backend go through `client()`. It refuses hosts not on the allowlist and any
destination that resolves to loopback, private, link-local, multicast or metadata addresses.
The explicit local client permits HTTP and private/loopback DNS only for LOCAL_INFERENCE_HOSTS;
literal IPs, link-local/metadata, multicast, reserved and unspecified addresses remain denied.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable

import httpx

from app.platform.config import get_settings
from app.platform.errors import Forbidden

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class EgressDenied(Forbidden):
    def __init__(self, host: str, reason: str) -> None:
        super().__init__("platform.egress_denied", detail=f"{host}: {reason}")


class _PinnedLocalTransport(httpx.BaseTransport):
    """Resolve configured hosts once and connect directly to validated IPs, preserving Host and TLS SNI."""

    def __init__(self, hosts: frozenset[str]) -> None:
        # Separate pools prevent TLS connections for different hostnames sharing an IP from being reused.
        self._local = {host: httpx.HTTPTransport() for host in hosts}
        self._default = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.lower().rstrip(".")
        transport = self._local.get(host)
        if transport is None:
            return self._default.handle_request(request)
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise EgressDenied(host, "DNS resolution failed") from exc
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        if not addresses:
            raise EgressDenied(host, "DNS resolution returned no addresses")
        for address in addresses:
            if not (_is_public(address) or _is_local(address)):
                raise EgressDenied(host, "resolves to a non-public address")
        for index, address in enumerate(addresses):
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=request.headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": request.url.host},
            )
            try:
                return transport.handle_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise
        raise AssertionError("validated addresses must not be empty")  # pragma: no cover

    def close(self) -> None:
        for transport in self._local.values():
            transport.close()
        self._default.close()


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _is_local(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (addr.is_private or addr.is_loopback) and not (
        addr.is_link_local or addr.is_multicast or addr.is_unspecified or (addr.is_reserved and not addr.is_loopback)
    )


def check_destination(host: str, allowlist: Iterable[str], resolve: bool = True, *, local: bool = False) -> None:
    host = (host or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        raise EgressDenied(host, "literal IP addresses are not allowed")
    except ValueError:
        pass
    if host not in set(allowlist):
        raise EgressDenied(host, "host not in EGRESS_ALLOWLIST")
    if resolve:
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise EgressDenied(host, "DNS resolution failed") from exc
        for info in infos:
            ip = str(info[4][0])
            if not (_is_public(ip) or (local and _is_local(ip))):
                raise EgressDenied(host, "resolves to a non-public address")


def _guard(allowlist: frozenset[str], resolve: bool, local_hosts: frozenset[str]):  # type: ignore[no-untyped-def]
    def hook(request: httpx.Request) -> None:
        local = request.url.host.lower().rstrip(".") in local_hosts
        if request.url.scheme != "https" and not (local and request.url.scheme == "http"):
            raise EgressDenied(request.url.host, "only https is allowed")
        # Local DNS is validated by the transport that pins the connection, even with resolve=False.
        check_destination(request.url.host, local_hosts if local else allowlist, resolve and not local, local=local)

    return hook


def _limit_size(response: httpx.Response) -> None:
    length = response.headers.get("content-length")
    if length and int(length) > MAX_RESPONSE_BYTES:
        response.close()
        raise EgressDenied(response.request.url.host, "response too large")


def client(
    allowlist: Iterable[str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    resolve: bool = True,
    *,
    local: bool = False,
) -> httpx.Client:
    hosts = frozenset(allowlist) if allowlist is not None else get_settings().egress_hosts
    local_hosts = get_settings().local_hosts if local else frozenset()
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        event_hooks={"request": [_guard(hosts, resolve, local_hosts)], "response": [_limit_size]},
        transport=_PinnedLocalTransport(local_hosts) if local_hosts else None,
        trust_env=not bool(local_hosts),
    )
