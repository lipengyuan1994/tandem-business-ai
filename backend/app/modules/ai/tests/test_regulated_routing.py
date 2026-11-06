"""P10-E0 routing boundary. Synthetic adapters and injected resolution only."""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import get_args

import pytest
import structlog
from structlog.testing import capture_logs

from app.modules.ai.gateway import router as router_module
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import (
    Router,
    check_routes_at_startup,
    policy_allows,
    reset_router_cache,
    set_router_for_tests,
)
from app.modules.ai.gateway.routes import (
    ModelClass,
    ProviderCfg,
    RouteEntry,
    RoutesFile,
    RoutesFileError,
    load_routes_file,
    parse_routes,
)
from app.modules.ai.gateway.types import ChatRequest, ProviderError
from app.modules.ai.public import status
from app.modules.ai.schemas import Policy
from app.modules.ai.tests.helpers import system_ctx
from app.modules.ai.usecases.copilot import _DisconnectRouter
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.db import transaction

CLASSES = get_args(ModelClass)


@pytest.fixture(autouse=True)
def fresh_router_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # App creation replaces processor lists; a cached logger can retain a prior app's list.
    # Bind a fresh lazy logger inside each capture context so the full suite observes actual warning events.
    monkeypatch.setattr(router_module, "log", structlog.get_logger())


@pytest.mark.parametrize("policy", get_args(Policy))
@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("vendor", ["vultr", "external", "local"])
@pytest.mark.parametrize("baa", [False, True])
def test_policy_matrix(policy: Policy, hosted: bool, vendor: str, baa: bool) -> None:
    # Include combinations forbidden at load, to exercise the policy's own fail-closed boundary too.
    cfg = ProviderCfg.model_construct(kind="openai_compat", hosted=hosted, vendor=vendor, baa=baa)
    expected = {
        "hosted_vultr": vendor in {"vultr", "local"},
        "hosted_any": True,
        "strict_private": not hosted,
        "regulated": not hosted and vendor == "local",
    }
    assert policy_allows(cfg, policy) == expected[policy]


@pytest.mark.parametrize("vendor", ["vultr", "external"])
def test_unhosted_requires_local_vendor_at_load(vendor: str) -> None:
    with pytest.raises(RoutesFileError, match=r"providers\.clinic\.vendor"):
        parse_routes(
            {"providers": {"clinic": {"kind": "openai_compat", "hosted": False, "vendor": vendor}}, "routes": {}}
        )


def route_config(host: str = "llm.internal", *, hosted: bool = False) -> RoutesFile:
    return RoutesFile(
        providers={
            "local": ProviderCfg(
                kind="openai_compat", vendor="local", hosted=hosted, base_url=f"http://{host}:8080/v1"
            ),
            "baa": ProviderCfg(kind="openai_compat", vendor="external", hosted=True, baa=True),
        },
        routes={c: [RouteEntry(provider=p, model=p) for p in ("baa", "local", "baa")] for c in CLASSES},
    )


@pytest.mark.parametrize("model_class", CLASSES)
def test_baa_never_used_even_as_fallback(model_class: ModelClass) -> None:
    local, hosted = FakeGateway([ProviderError("timeout")]), FakeGateway(["must not run"])
    router = Router(route_config(), {"local": local, "baa": hosted}, resolver=lambda _host: ["10.1.2.3"])
    assert [r.provider for r in router.allowed(model_class, "regulated")] == ["local"]
    with pytest.raises(ProviderError, match="all 1 route"):
        router.complete(model_class, "regulated", ChatRequest(messages=[]))
    assert len(local.calls) == 1 and hosted.calls == []


@pytest.mark.parametrize(
    ("host", "addresses"),
    [
        ("127.0.0.1", ["127.0.0.1"]),
        ("[::1]", ["::1"]),
        ("10.1.2.3", ["10.1.2.3"]),
        ("172.16.0.1", ["172.16.0.1"]),
        ("192.168.9.1", ["192.168.9.1"]),
        ("llm", ["172.20.0.4"]),
        ("llm.internal", ["fd12::1", "10.2.3.4"]),
        ("[fc00::1]", ["fc00::1"]),
        ("169.254.10.1", ["169.254.10.1"]),
        ("[fe80::1]", ["fe80::1"]),
        ("[::ffff:192.168.1.1]", ["::ffff:192.168.1.1"]),
    ],
)
def test_private_addresses_and_resolved_docker_names_allowed(host: str, addresses: list[str]) -> None:
    seen = []

    def resolve(hostname: str) -> list[str]:
        seen.append(hostname)
        return addresses

    router = Router(route_config(host), {"local": FakeGateway([])}, resolver=resolve)
    for c in CLASSES:
        assert router.allowed(c, "regulated")
    assert seen == [host.strip("[]")]  # cached across classes/attempts


@pytest.mark.parametrize(
    "addresses",
    [
        ["8.8.8.8"],
        ["2606:4700:4700::1111"],
        ["10.0.0.1", "8.8.8.8"],
        ["100.64.0.1"],
        ["192.0.2.1"],
        ["2001:db8::1"],
        ["0.0.0.0"],  # noqa: S104 (address fixture)
        ["::"],
        ["::ffff:8.8.8.8"],
        ["224.0.0.1"],
        ["not-an-address"],
        [],
    ],
)
def test_public_special_and_mixed_addresses_refused_without_logging_secrets(addresses: list[str]) -> None:
    routes = route_config("llm.internal")
    routes.providers["local"].base_url = "http://user:synthetic-secret@llm.internal:8080/v1?key=hidden"
    with capture_logs() as events:
        router = Router(routes, {"local": FakeGateway([])}, resolver=lambda _host: addresses)
        assert not router.allowed("agent", "regulated")
    event = next(e for e in events if e["event"] == "ai_local_provider_public_address")
    assert event == {"event": "ai_local_provider_public_address", "host": "llm.internal", "log_level": "warning"}
    assert "synthetic-secret" not in str(events) and "hidden" not in str(events)


def test_resolver_failure_fails_closed_at_regulated_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")

    def broken(_host: str) -> list[str]:
        raise socket.gaierror("synthetic resolution failure")

    with capture_logs() as events:
        router = Router(route_config(), {"local": FakeGateway([])}, resolver=broken)
    assert any(e["event"] == "ai_local_provider_public_address" for e in events)
    assert not router.allowed("stt", "regulated")
    assert router.allowed("stt", "strict_private")  # no new strict_private address restriction


def test_hosted_local_vendor_is_valid_but_not_local_under_regulated() -> None:
    router = Router(route_config(hosted=True), {"local": FakeGateway([])}, resolver=lambda _host: ["10.0.0.1"])
    assert router.allowed("embed", "hosted_any")
    assert not router.allowed("embed", "regulated")


@pytest.mark.parametrize("baa", [False, True])
def test_legacy_baa_key_loads_and_warns_under_regulated(baa: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    routes = parse_routes(
        {
            "providers": {"old": {"kind": "openai_compat", "vendor": "external", "baa": baa}},
            "routes": {"agent": [{"provider": "old", "model": "old"}]},
        }
    )
    with capture_logs() as events:
        Router(routes, {})
    assert not events  # standard profile does not warn
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    with capture_logs() as events:
        router = Router(routes, {"old": FakeGateway([])})
    assert {"event": "ai_routes_baa_ignored", "provider": "old", "log_level": "warning"} in events
    assert not router.allowed("agent", "regulated")


def test_regulated_startup_warns_even_outside_production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "old.yaml"
    path.write_text("providers: {old: {kind: openai_compat, vendor: external, baa: true}}\nroutes: {}\n")
    monkeypatch.setattr(get_settings(), "ai_routes_file", str(path))
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    assert not get_settings().is_prod
    set_router_for_tests(None)  # startup must load this file, not a preceding test's override
    reset_router_cache()
    try:
        with capture_logs() as events:
            check_routes_at_startup()
        assert {"event": "ai_routes_baa_ignored", "provider": "old", "log_level": "warning"} in events
    finally:
        reset_router_cache()


def test_local_example_loads_for_every_class_without_keys_or_prices() -> None:
    path = Path(__file__).resolve().parents[5] / "deploy/config/ai-routes.local.example.yaml"
    routes = load_routes_file(path)
    assert set(routes.routes) == set(CLASSES)
    assert all(p.vendor == "local" and not p.hosted and not p.api_key_env for p in routes.providers.values())
    assert all(not e.price_in_per_m and not e.price_out_per_m for entries in routes.routes.values() for e in entries)
    router = Router(routes, {p: FakeGateway([]) for p in routes.providers}, resolver=lambda _host: ["10.0.0.2"])
    assert all(router.allowed(c, "regulated") for c in CLASSES)


def test_status_has_specific_availability_for_every_class(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    routes = route_config()
    routes.routes["stt"] = [RouteEntry(provider="baa", model="stt")]
    router = Router(routes, {"local": FakeGateway([]), "baa": FakeGateway([])}, resolver=lambda _host: ["192.168.0.2"])
    set_router_for_tests(router)
    ctx = system_ctx(make_business())
    try:
        with transaction(ctx) as db:
            result = status(db, ctx)
        assert result.enabled
        assert set(result.model_classes) == set(CLASSES)
        assert result.model_classes["embed"].enabled
        assert not result.model_classes["stt"].enabled
        assert result.model_classes["stt"].reason == "regulated: no local route for stt"
        routes.routes["agent"] = [RouteEntry(provider="baa", model="agent")]
        with transaction(ctx) as db:
            result = status(db, ctx)
        assert not result.enabled and result.reason == "ai.disabled_by_policy"
        assert result.model_classes["agent"].reason == "regulated: no local route for agent"
    finally:
        set_router_for_tests(None)


def test_copilot_wrapper_reuses_verified_addresses_without_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    inner = Router(route_config(), {"local": FakeGateway(["local reply"])}, resolver=lambda _host: ["10.0.0.2"])

    def unexpected_dns(_host: str) -> list[str]:
        pytest.fail("a router decorator must reuse the verified inner router")

    monkeypatch.setattr(router_module, "resolve_host", unexpected_dns)
    wrapped = _DisconnectRouter(inner, threading.Event())
    assert wrapped.allowed("agent", "regulated") == inner.allowed("agent", "regulated")
    assert wrapped.complete("agent", "regulated", ChatRequest(messages=[]))[0].text == "local reply"
