"""Router, route file, policies, pricing and the fake provider (AI-03). No network, no model."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import SecretStr

from app.modules.ai.gateway import openai_compat as openai_mod
from app.modules.ai.gateway.anthropic import AnthropicAdapter
from app.modules.ai.gateway.fake import FakeGateway, ScriptFileAdapter, fake_router
from app.modules.ai.gateway.openai_compat import OpenAICompatAdapter
from app.modules.ai.gateway.pricing import cost_micros
from app.modules.ai.gateway.router import (
    FakeProviderInProduction,
    PolicyBlocked,
    Router,
    check_routes_at_startup,
    effective_policy,
    load_router,
    reset_router_cache,
    set_router_for_tests,
)
from app.modules.ai.gateway.routes import RouteEntry, RoutesFile, RoutesFileError, load_routes_file, parse_routes
from app.modules.ai.gateway.types import ChatMessage, ChatRequest, ProviderError, Usage
from app.modules.ai.public import status
from app.modules.ai.tests.helpers import system_ctx
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.db import transaction

REPO = Path(__file__).resolve().parents[5]
DEPLOY_CONFIG = REPO / "deploy" / "config"
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}

PROVIDERS: dict[str, dict[str, Any]] = {
    "vultr": {"kind": "openai_compat", "base_url": "https://api.vultrinference.com/v1",
              "api_key_env": "VULTR_INFERENCE_API_KEY", "vendor": "vultr"},
    "openai": {"kind": "openai_compat", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY",
               "vendor": "external"},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com", "api_key_env": "ANTHROPIC_API_KEY",
                  "vendor": "external", "baa": True},
    "local": {"kind": "openai_compat", "base_url": "http://llm-local:8080/v1", "hosted": False, "vendor": "local"},
}  # fmt: skip


def routes_file(**routes: list[str]) -> RoutesFile:
    """routes_file(agent=["vultr", "openai"]) → each provider gets an entry with model '<provider>-m'."""
    return parse_routes(
        {
            "providers": PROVIDERS,
            "routes": {mc: [{"provider": p, "model": f"{p}-m"} for p in ps] for mc, ps in routes.items()},
        }
    )


def gateways(*names: str, script: list[Any] | None = None) -> dict[str, FakeGateway]:
    out = {}
    for n in names:
        gw = FakeGateway(list(script) if script is not None else [f"from {n}"])
        gw.name = n
        out[n] = gw
    return out


def named(name: str, *items: Any) -> FakeGateway:
    gw = FakeGateway(list(items))
    gw.name = name
    return gw


def allowed_providers(router: Router, policy: Any) -> list[str]:
    return [e.provider for e in router.allowed("agent", policy)]


def req(schema: dict[str, Any] | None = None) -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content="hi")], json_schema=schema)


KEY_SETTINGS = ("vultr_inference_api_key", "openai_api_key", "anthropic_api_key", "gemini_api_key")


@pytest.fixture(autouse=True)
def _clean_router(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for env in KEY_SETTINGS:  # independent of keys in the developer's environment
        monkeypatch.setattr(get_settings(), env, SecretStr(""))
    reset_router_cache()
    set_router_for_tests(None)
    yield
    reset_router_cache()
    set_router_for_tests(None)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in KEY_SETTINGS:
        monkeypatch.setattr(get_settings(), env, SecretStr("test-key-not-real"))


def write_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: dict[str, Any] | str) -> Path:
    path = tmp_path / "ai-routes.yaml"
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "ai_routes_file", str(path))
    return path


# ---------------------------------------------------------------------------------------------- policies


def test_policy_hosted_vultr_allows_only_vultr_and_local() -> None:
    router = Router(routes_file(agent=["openai", "vultr", "anthropic", "local"]), gateways(*PROVIDERS))  # type: ignore[arg-type]
    assert allowed_providers(router, "hosted_vultr") == ["vultr", "local"]
    assert allowed_providers(router, "hosted_any") == ["openai", "vultr", "anthropic", "local"]


def test_policy_strict_private_only_unhosted() -> None:
    router = Router(routes_file(agent=["vultr", "openai", "local"]), gateways(*PROVIDERS))  # type: ignore[arg-type]
    assert allowed_providers(router, "strict_private") == ["local"]
    no_local = Router(routes_file(agent=["vultr", "openai"]), gateways(*PROVIDERS))  # type: ignore[arg-type]
    with pytest.raises(PolicyBlocked):
        no_local.complete("agent", "strict_private", req())


def test_policy_regulated_only_local_and_forced_by_data_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    router = Router(
        routes_file(agent=["vultr", "openai", "anthropic", "local"]),
        gateways(*PROVIDERS),
        resolver=lambda _host: ["172.20.0.2"],
    )  # type: ignore[arg-type]
    assert allowed_providers(router, "regulated") == ["local"]  # BAA no longer grants access

    ctx = system_ctx(make_business())  # business policy: hosted_vultr (default)
    with transaction(ctx) as db:
        assert effective_policy(db, ctx) == "hosted_vultr"
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    with transaction(ctx) as db:
        assert effective_policy(db, ctx) == "regulated"


def test_fake_provider_passes_development_policies_but_not_regulated() -> None:
    gw = FakeGateway(["ok"] * 4)
    router = fake_router(gw)
    for policy in ("hosted_vultr", "hosted_any", "strict_private"):
        assert router.complete("agent", policy, req())[0].text == "ok"  # type: ignore[arg-type]
    with pytest.raises(PolicyBlocked):
        router.complete("agent", "regulated", req())


# ------------------------------------------------------------------------------------------ adapters/keys


def test_missing_api_key_skips_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_routes(tmp_path, monkeypatch, {"providers": PROVIDERS, "routes": {
        "agent": [{"provider": "vultr", "model": "v"}, {"provider": "openai", "model": "o"},
                  {"provider": "local", "model": "l"}],
    }})  # fmt: skip
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("test-key-not-real"))
    monkeypatch.setattr(get_settings(), "vultr_inference_api_key", SecretStr(""))
    router = load_router()
    assert router is not None
    assert set(router.adapters) == {"openai", "local"}  # local needs no key (api_key_env null)
    assert allowed_providers(router, "hosted_any") == ["openai", "local"]
    assert allowed_providers(router, "hosted_vultr") == ["local"]


def test_local_provider_uses_local_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys: None) -> None:
    write_routes(tmp_path, monkeypatch, {"providers": PROVIDERS, "routes": {}})
    router = load_router()
    assert router is not None
    local, vultr, anth = router.adapters["local"], router.adapters["vultr"], router.adapters["anthropic"]
    assert isinstance(local, OpenAICompatAdapter) and local.local is True
    assert isinstance(vultr, OpenAICompatAdapter) and vultr.local is False
    assert isinstance(anth, AnthropicAdapter)

    seen: list[dict[str, Any]] = []

    def fake_client(**kwargs: Any) -> Any:
        seen.append(kwargs)
        raise ProviderError("unavailable")  # stop before any HTTP

    monkeypatch.setattr(openai_mod, "client", fake_client)
    for adapter in (local, vultr):
        with pytest.raises(ProviderError):
            adapter.chat(req())
    assert seen == [{"local": True}, {"local": False}]


# ------------------------------------------------------------------------------------------ complete()


def test_fallback_on_timeout_and_429_but_not_on_bad_request() -> None:
    gws = gateways("vultr", "openai", "anthropic")
    gws["vultr"] = named("vultr", ProviderError("timeout"))
    gws["openai"] = named("openai", ProviderError("rate_limited", retry_after_s=3))
    router = Router(routes_file(agent=["vultr", "openai", "anthropic"]), gws)  # type: ignore[arg-type]
    result, entry = router.complete("agent", "hosted_any", req())
    assert (result.text, entry.provider) == ("from anthropic", "anthropic")
    assert [g.calls[0].model for g in gws.values()] == ["vultr-m", "openai-m", "anthropic-m"]

    gws = gateways("vultr", "openai")
    gws["vultr"] = named("vultr", ProviderError("bad_request"))
    router = Router(routes_file(agent=["vultr", "openai"]), gws)  # type: ignore[arg-type]
    with pytest.raises(ProviderError) as err:
        router.complete("agent", "hosted_any", req())
    assert err.value.kind == "bad_request"
    assert gws["openai"].calls == []  # no fallback on a non-transient error

    gws = gateways("vultr", "openai")
    gws["vultr"] = named("vultr", ProviderError("auth"))
    with pytest.raises(ProviderError) as err:
        Router(routes_file(agent=["vultr", "openai"]), gws).complete("agent", "hosted_any", req())  # type: ignore[arg-type]
    assert err.value.kind == "auth" and gws["openai"].calls == []


def test_fallback_never_reaches_a_provider_the_policy_excludes() -> None:
    gws = gateways("vultr", "openai")
    gws["vultr"] = named("vultr", ProviderError("server_error"))
    router = Router(routes_file(agent=["vultr", "openai"]), gws)  # type: ignore[arg-type]
    with pytest.raises(ProviderError) as err:
        router.complete("agent", "hosted_vultr", req())
    assert err.value.kind == "unavailable" and gws["openai"].calls == []


def test_all_routes_failed_raises_unavailable() -> None:
    gws = gateways("vultr", "local")
    gws["vultr"] = named("vultr", ProviderError("server_error"))
    gws["local"] = named("local", ProviderError("unavailable"))
    router = Router(routes_file(agent=["vultr", "local"]), gws)  # type: ignore[arg-type]
    with pytest.raises(ProviderError) as err:
        router.complete("agent", "hosted_vultr", req())
    assert err.value.kind == "unavailable"


def test_no_allowed_route_raises_policy_blocked() -> None:
    router = Router(routes_file(agent=["openai"]), gateways("openai"))  # type: ignore[arg-type]
    with pytest.raises(PolicyBlocked):
        router.complete("agent", "hosted_vultr", req())
    with pytest.raises(PolicyBlocked):
        router.complete("writer", "hosted_any", req())  # class without routes
    no_adapter = Router(routes_file(agent=["vultr"]), {})
    with pytest.raises(PolicyBlocked):
        no_adapter.complete("agent", "hosted_vultr", req())


def test_json_mode_chosen_from_route_entry() -> None:
    routes = parse_routes({"providers": PROVIDERS, "routes": {"agent": [
        {"provider": "vultr", "model": "a", "json_schema": True}, {"provider": "local", "model": "b"},
    ]}})  # fmt: skip
    gws = gateways("vultr", "local", script=["{}", "{}"])
    router = Router(routes, gws)  # type: ignore[arg-type]
    router.complete("agent", "hosted_vultr", req(SCHEMA))
    router.complete("agent", "hosted_vultr", req())
    assert [c.json_mode for c in gws["vultr"].calls] == ["json_schema", "text"]

    gws["vultr"] = named("vultr", ProviderError("timeout"))
    router.complete("agent", "hosted_vultr", req(SCHEMA))
    assert gws["local"].calls[-1].json_mode == "json_object"  # entry without json_schema proof
    assert gws["local"].calls[-1].model == "b"


# ------------------------------------------------------------------------------------------ pricing


def test_cost_micros_rounding() -> None:
    entry = RouteEntry(provider="vultr", model="m", price_in_per_m=0.2, price_out_per_m=0.6)
    assert cost_micros(entry, Usage(input_tokens=1000, output_tokens=500)) == 500  # 200 + 300
    half = RouteEntry(provider="vultr", model="m", price_in_per_m=0.5, price_out_per_m=0.0)
    assert cost_micros(half, Usage(input_tokens=1, output_tokens=0)) == 1  # 0.5 rounds half up
    assert cost_micros(half, Usage(input_tokens=3, output_tokens=0)) == 2  # 1.5 → 2
    third = RouteEntry(provider="vultr", model="m", price_in_per_m=0.35, price_out_per_m=0.35)
    assert cost_micros(third, Usage(input_tokens=1, output_tokens=0)) == 0  # 0.35 → 0
    assert cost_micros(RouteEntry(provider="x", model="m"), Usage(input_tokens=10**6, output_tokens=10**6)) == 0
    big = RouteEntry(provider="x", model="m", price_in_per_m=3.0, price_out_per_m=15.0)
    assert cost_micros(big, Usage(input_tokens=1_000_000, output_tokens=1_000_000)) == 18_000_000


# ------------------------------------------------------------------------------------------ route file


def test_routes_file_validation_errors_name_key(tmp_path: Path) -> None:
    def problem(data: Any) -> str:
        with pytest.raises(RoutesFileError) as err:
            parse_routes(data)
        return str(err.value)

    assert "providers.vultr.kind" in problem(
        {"providers": {"vultr": {"kind": "grpc", "vendor": "vultr"}}, "routes": {}}
    )
    assert "providers.vultr.vendor" in problem({"providers": {"vultr": {"kind": "openai_compat"}}, "routes": {}})
    assert "routes.agent.0.provider" in problem(
        {"providers": PROVIDERS, "routes": {"agent": [{"provider": "nope", "model": "m"}]}}
    )
    assert "routes.chat" in problem({"providers": PROVIDERS, "routes": {"chat": []}})
    assert "limits.max_steps" in problem({"providers": PROVIDERS, "routes": {}, "limits": {"max_steps": 0}})
    assert "policy_default" in problem({"providers": PROVIDERS, "routes": {}, "policy_default": "hosted_vultr"})
    assert "(root)" in problem(["not", "a", "mapping"])
    bad = tmp_path / "ai-routes.yaml"
    bad.write_text("providers: [unclosed\n", encoding="utf-8")
    with pytest.raises(RoutesFileError, match="not valid YAML"):
        load_routes_file(bad)


def test_example_and_dev_route_files_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    example = load_routes_file(DEPLOY_CONFIG / "ai-routes.example.yaml")
    assert {"vultr", "openai", "gemini", "anthropic", "local"} <= set(example.providers)
    assert all(p.kind != "fake" and not p.baa for p in example.providers.values())
    assert example.routes["agent"] and example.routes["safety"]

    dev = load_routes_file(DEPLOY_CONFIG / "ai-routes.dev.yaml")
    assert {p.kind for p in dev.providers.values()} == {"fake"}
    script = DEPLOY_CONFIG / "ai-fake-script.dev.yaml"
    adapter = ScriptFileAdapter("fake", script)
    replies = yaml.safe_load(script.read_text(encoding="utf-8"))
    use_cases = ("intake", "copilot", "summary", "reorder_note", "briefing", "procurement", "campaign_draft",
                 "payment_followup", "safety", "grounding", "dictation_stt", "dictation_polish")  # fmt: skip
    assert set(replies) == set(use_cases)
    for use_case in use_cases:
        assert adapter.chat(ChatRequest(messages=[], use_case=use_case)).text == replies[use_case][0]


def test_fake_script_cycles_per_use_case(tmp_path: Path) -> None:
    script = tmp_path / "script.yaml"
    script.write_text("summary: [one, two]\nbriefing: [b]\n", encoding="utf-8")
    adapter = ScriptFileAdapter("fake", script)

    def say(use_case: str | None) -> str:
        return adapter.chat(ChatRequest(messages=[], use_case=use_case)).text

    assert [say("summary"), say("briefing"), say("summary"), say("summary")] == ["one", "b", "two", "one"]
    with pytest.raises(ProviderError) as err:
        say("intake")
    assert err.value.kind == "bad_request"
    script.write_text("summary: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="summary"):
        ScriptFileAdapter("fake", script)


def test_fake_provider_refused_in_prod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_routes(tmp_path, monkeypatch, (DEPLOY_CONFIG / "ai-routes.dev.yaml").read_text(encoding="utf-8"))
    monkeypatch.setattr(get_settings(), "ai_fake_script_file", str(DEPLOY_CONFIG / "ai-fake-script.dev.yaml"))
    assert load_router() is not None  # dev: fine
    check_routes_at_startup()  # not prod: no-op
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    with pytest.raises(FakeProviderInProduction):
        load_router()
    with pytest.raises(FakeProviderInProduction):
        check_routes_at_startup()
    with pytest.raises(RuntimeError):
        ScriptFileAdapter("fake", DEPLOY_CONFIG / "ai-fake-script.dev.yaml")


def test_load_router_none_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keys: None) -> None:
    monkeypatch.setattr(get_settings(), "ai_routes_file", str(tmp_path / "missing.yaml"))
    assert load_router() is None
    write_routes(tmp_path, monkeypatch, "providers: {vultr: {kind: nope}}\n")
    assert load_router() is None  # invalid → None (logged), the app keeps running
    path = write_routes(tmp_path, monkeypatch, {"providers": PROVIDERS, "routes": {"agent": [
        {"provider": "vultr", "model": "m"}]}})  # fmt: skip
    first = load_router()
    assert first is not None and load_router() is first  # cached per process
    reset_router_cache()
    assert load_router() is not first
    override = fake_router(FakeGateway([]))
    set_router_for_tests(override)
    assert load_router() is override
    set_router_for_tests(None)
    path.unlink()
    assert load_router() is None


# ------------------------------------------------------------------------------------------ status


def test_status_follows_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = system_ctx(make_business())
    write_routes(tmp_path, monkeypatch, {"providers": PROVIDERS, "routes": {"agent": [
        {"provider": "openai", "model": "o"}, {"provider": "vultr", "model": "v"}]}})  # fmt: skip
    with transaction(ctx) as db:
        assert status(db, ctx).reason == "ai.not_configured"  # no provider key at all
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("test-key-not-real"))
    with transaction(ctx) as db:
        result = status(db, ctx)
    assert (result.enabled, result.reason) == (False, "ai.disabled_by_policy")  # hosted_vultr excludes openai
    monkeypatch.setattr(get_settings(), "vultr_inference_api_key", SecretStr("test-key-not-real"))
    with transaction(ctx) as db:
        result = status(db, ctx)
    assert (result.enabled, result.reason) == (True, None)


def test_status_enabled_with_dev_route_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_routes(tmp_path, monkeypatch, (DEPLOY_CONFIG / "ai-routes.dev.yaml").read_text(encoding="utf-8"))
    monkeypatch.setattr(get_settings(), "ai_fake_script_file", str(DEPLOY_CONFIG / "ai-fake-script.dev.yaml"))
    ctx = system_ctx(make_business())
    with transaction(ctx) as db:
        assert status(db, ctx).enabled is True
