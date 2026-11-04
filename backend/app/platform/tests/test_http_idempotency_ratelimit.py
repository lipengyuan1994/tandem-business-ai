"""P1-09: egress guard, idempotency keys, rate limits."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from app.platform import http, idempotency, ratelimit
from app.platform.db import transaction
from app.platform.errors import Conflict, RateLimited
from app.platform.testing.auth import ctx_for

ALLOW = ["api.example-provider.test", "localhost"]


@pytest.mark.parametrize(
    "host",
    ["169.254.169.254", "127.0.0.1", "10.0.0.5", "[::1]", "evil.example.org"],
)
def test_http_client_blocks_private_metadata_literal_and_unlisted_hosts(host: str) -> None:
    with http.client(ALLOW, resolve=False) as client, pytest.raises(http.EgressDenied):
        client.get(f"https://{host}/latest/meta-data")


def test_http_client_blocks_allowlisted_name_resolving_to_private_ip() -> None:
    with pytest.raises(http.EgressDenied, match="non-public"):
        http.check_destination("localhost", ALLOW, resolve=True)


def test_http_client_requires_https() -> None:
    with http.client(ALLOW, resolve=False) as client, pytest.raises(http.EgressDenied, match="https"):
        client.get("http://api.example-provider.test/v1")


@pytest.mark.parametrize("local", [False, True])
@respx.mock
def test_http_client_allows_listed_public_host(local: bool) -> None:
    respx.get("https://api.example-provider.test/v1/models").mock(return_value=httpx.Response(200, json={"data": []}))
    with http.client(ALLOW, resolve=False, local=local) as client:
        assert client.get("https://api.example-provider.test/v1/models").json() == {"data": []}


@respx.mock
def test_local_client_allows_only_listed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "local_inference_hosts", " LOCALHOST, inference.example.test, ")
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 8080))])
    respx.get("http://127.0.0.1:8080/health").mock(return_value=httpx.Response(200))
    respx.get("https://127.0.0.1/v1").mock(return_value=httpx.Response(200))
    with http.client(local=True) as client:
        assert client.get("http://localhost:8080/health").status_code == 200
        assert client.get("https://inference.example.test/v1").status_code == 200
        assert not client.follow_redirects
        for url in ("http://other.example.test", "http://api.example-provider.test", "https://other.example.test"):
            with pytest.raises(http.EgressDenied):
                client.get(url)
        with pytest.raises(http.EgressDenied):
            client.get("https://api.example-provider.test")  # still refuses private DNS outside the local list
        with pytest.raises(http.EgressDenied):
            client.get("ftp://localhost/health")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "::1", "fd00::1"])
@respx.mock
def test_local_client_accepts_private_dns(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "local_inference_hosts", "inference.example.test")
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (ip, 8080))])
    pinned = httpx.URL("http://inference.example.test:8080").copy_with(host=ip)
    respx.get(pinned).mock(return_value=httpx.Response(200))
    with http.client(local=True) as client:
        assert client.get("http://inference.example.test:8080").status_code == 200


@pytest.mark.parametrize("ip", ["169.254.169.254", "224.0.0.1", "0.0.0.0", "240.0.0.1", "fe80::1"])  # noqa: S104
def test_local_client_keeps_other_address_restrictions(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "local_inference_hosts", "inference.example.test,127.0.0.1")
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (ip, 8080))])
    with http.client(local=True) as client:
        with pytest.raises(http.EgressDenied):
            client.get("http://inference.example.test:8080")
        with pytest.raises(http.EgressDenied):
            client.get("http://127.0.0.1:8080")


def test_default_client_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "local_inference_hosts", "localhost")
    with http.client() as client:
        for url in ("http://localhost:8080", "https://localhost:8080", "http://api.example-provider.test"):
            with pytest.raises(http.EgressDenied):
                client.get(url)
    monkeypatch.setattr(get_settings(), "local_inference_hosts", "")
    with http.client(local=True) as client, pytest.raises(http.EgressDenied):
        client.get("http://localhost:8080")


@respx.mock
def test_local_client_preserves_redirect_timeout_and_size_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "local_inference_hosts", "localhost")
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 8080))])
    respx.get("http://127.0.0.1:8080/redirect").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    )
    respx.get("http://127.0.0.1:8080/large").mock(
        return_value=httpx.Response(200, headers={"content-length": str(http.MAX_RESPONSE_BYTES + 1)})
    )
    with http.client(local=True) as client:
        assert client.timeout == http.DEFAULT_TIMEOUT
        assert client.get("http://localhost:8080/redirect").status_code == 302
        with pytest.raises(http.EgressDenied, match="response too large"):
            client.get("http://localhost:8080/large")


def test_local_client_dns_failure_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform.config import get_settings

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise http.socket.gaierror("unresolvable")

    monkeypatch.setattr(get_settings(), "local_inference_hosts", "inference.example.test")
    monkeypatch.setattr(http.socket, "getaddrinfo", fail)
    with http.client(local=True) as client, pytest.raises(http.EgressDenied, match="DNS resolution failed"):
        client.get("http://inference.example.test:8080")


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("suffix", ["", "."])
@pytest.mark.parametrize("proxy", [False, True])
def test_local_transport_connects_only_to_validated_address(
    monkeypatch: pytest.MonkeyPatch, scheme: str, suffix: str, proxy: bool
) -> None:
    from app.platform.config import get_settings

    host = "inference.example.test"
    lookups: list[str] = []
    connections: list[tuple[str, int]] = []
    sent = bytearray()
    tls_names: list[str] = []

    def resolve(name: str, port: int, *args: Any, **kwargs: Any) -> Any:
        if name.rstrip(".") == host:
            lookups.append(name)
            address = "127.0.0.1" if len(lookups) == 1 else "169.254.169.254"
        else:
            assert name == "127.0.0.1", f"unexpected connection lookup: {name}"
            address = name
        return [(http.socket.AF_INET, http.socket.SOCK_STREAM, http.socket.IPPROTO_TCP, "", (address, port))]

    class OfflineSocket:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"

        def settimeout(self, timeout: Any) -> None:
            pass

        def setsockopt(self, *args: Any) -> None:
            pass

        def connect(self, address: tuple[str, int]) -> None:
            connections.append(address)

        def send(self, data: bytes) -> int:
            sent.extend(data)
            return len(data)

        def recv(self, size: int) -> bytes:
            data, self.response = self.response[:size], self.response[size:]
            return data

        def close(self) -> None:
            pass

    def start_tls(context: Any, sock: Any, *, server_hostname: str, **kwargs: Any) -> Any:
        tls_names.append(server_hostname)
        return sock

    monkeypatch.setattr(get_settings(), "local_inference_hosts", host)
    # Run the real HTTPX/HTTPCore transport and socket.create_connection, but replace DNS, sockets
    # and the TLS handshake. No socket can reach the network, including on the vulnerable code.
    with monkeypatch.context() as network:
        import ssl

        for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            network.delenv(variable, raising=False)
        network.setenv("NO_PROXY", "")
        network.setenv("no_proxy", "")
        if proxy:
            network.setenv("ALL_PROXY", "http://proxy.example.test:3128")
        network.setattr(http.socket, "getaddrinfo", resolve)
        network.setattr(http.socket, "socket", OfflineSocket)
        network.setattr(ssl.SSLContext, "wrap_socket", start_tls)
        url = f"{scheme}://{host}{suffix}:8080/health"
        with http.client(local=True) as client:
            response = client.get(url)
            assert response.status_code == 200 and response.text == "ok"
            assert str(response.request.url) == url
    assert connections == [("127.0.0.1", 8080)]
    assert len(lookups) == 1
    assert f"Host: {host}{suffix}:8080\r\n".lower().encode() in bytes(sent).lower()
    assert tls_names == ([host + suffix] if scheme == "https" else [])


@pytest.mark.parametrize("address", ["169.254.0.1", "169.254.169.254", "169.254.255.254"])
def test_local_transport_never_connects_to_link_local_when_resolution_check_disabled(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    from app.platform.config import get_settings

    def connect(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("the local transport must reject link-local DNS before connecting")

    monkeypatch.setattr(get_settings(), "local_inference_hosts", "inference.example.test")
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 8080))])
    monkeypatch.setattr(http.socket, "create_connection", connect)
    with http.client(local=True, resolve=False) as client, pytest.raises(http.EgressDenied):
        client.get("http://inference.example.test:8080")


def test_idempotency_returns_original_response_and_rejects_changed_body(business: Any) -> None:
    ctx = ctx_for(business)
    body = {"name": "Ada"}
    with transaction(ctx) as db:
        assert idempotency.lookup(db, ctx, "key-12345678", body) is None
        idempotency.store(db, ctx, "key-12345678", body, 201, {"id": "c1"})
    with transaction(ctx) as db:
        cached = idempotency.lookup(db, ctx, "key-12345678", body)
        assert cached is not None and cached.status == 201 and cached.body == {"id": "c1"}
        with pytest.raises(Conflict):
            idempotency.lookup(db, ctx, "key-12345678", {"name": "Eve"})


def test_rate_limit_window(business: Any, freeze_time: Any) -> None:
    freeze_time(datetime(2026, 11, 5, 17, 0, 5, tzinfo=UTC))
    with transaction(ctx_for(business)) as db:
        for _ in range(3):
            ratelimit.hit(db, "public:ip:203.0.113.9", limit=3, window_seconds=60)
        with pytest.raises(RateLimited):
            ratelimit.hit(db, "public:ip:203.0.113.9", limit=3, window_seconds=60)
    freeze_time(datetime(2026, 11, 5, 17, 1, 5, tzinfo=UTC))
    with transaction(ctx_for(business)) as db:
        assert ratelimit.hit(db, "public:ip:203.0.113.9", limit=3, window_seconds=60) == 1
