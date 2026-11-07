"""AI-14: API permission/tenant boundaries, quota and policy with zero external traffic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.ai.api import router as api_router
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import (
    FakeProviderInProduction,
    Router,
    build_adapters,
    reset_router_cache,
    set_router_for_tests,
)
from app.modules.ai.gateway.routes import ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.models import AiUsageDaily
from app.modules.ai.permissions import AI_SYSTEM_PERMISSIONS
from app.modules.ai.tests.helpers import fake_router_by_class, fill_all_tables, step_final, system_ctx
from app.modules.ai.tests.test_copilot import new_thread, send
from app.modules.ai.tests.test_intake import inbound
from app.modules.ai.tests.tool_helpers import Demo, seed_demo
from app.modules.ai.usecases import intake
from app.modules.org.testing import make_business
from app.modules.purchasing import public as purchasing
from app.platform import clock
from app.platform.config import get_settings
from app.platform.db import transaction
from app.platform.registry import Registry, set_registry
from app.platform.testing.auth import client_for

BASE = "/api/v1/ai"
ALL_PERMISSIONS = AI_SYSTEM_PERMISSIONS | {"ai.settings.read", "purchasing.po.write"}


@pytest.fixture(autouse=True)
def clean_router():
    set_router_for_tests(None)
    reset_router_cache()
    yield
    set_router_for_tests(None)
    reset_router_cache()


@pytest.fixture
def demo(owner_engine: Engine, registry: Registry) -> Demo:
    data = seed_demo(owner_engine)
    set_registry(registry)
    return data


def endpoints() -> list[tuple[str, str, dict | None]]:
    ident = str(uuid.uuid4())
    return [
        ("GET", "/agents", None),
        ("GET", "/agents/addressable", None),
        ("PATCH", "/agents/inventory", {"version": 0, "enabled": False}),
        ("GET", "/agents/inventory/activity", None),
        ("GET", f"/chains/{ident}", None),
        ("GET", f"/customer-care/cases/{ident}", None),
        ("POST", f"/customer-care/cases/{ident}/resolve", {"version": 1}),
        ("GET", "/work-queue", None),
        ("GET", "/exceptions", None),
        (
            "POST",
            f"/copilot/threads/{ident}/proposals/{ident}/confirm",
            {"revision_id": ident, "payload_hash": "0" * 64},
        ),
        ("GET", "/autonomy/rules", None),
        ("PUT", "/autonomy/rules", {"action_type": "inventory.create_reorder_draft", "level": "ask", "version": 0}),
        ("POST", "/autonomy/pause", None),
        ("POST", "/autonomy/resume", None),
        ("GET", "/autonomy/activity", None),
        ("GET", "/status", None),
        ("GET", "/runs", None),
        ("GET", "/usage", None),
        ("GET", "/briefing", None),
        ("GET", "/copilot/threads", None),
        ("POST", "/copilot/threads", {}),
        ("GET", f"/copilot/threads/{ident}", None),
        ("POST", f"/copilot/threads/{ident}/messages", {"content": "Inspect only."}),
        ("POST", f"/conversations/{ident}/summary", None),
        ("POST", f"/procurement/items/{ident}/request-quotes", None),
        ("POST", f"/procurement/quote-requests/{ident}/extract", {"supplier_id": ident, "text": "Inspect only."}),
        ("POST", f"/campaigns/{ident}/draft", {}),
    ]


def test_every_ai_endpoint_requires_its_permission(registry: Registry) -> None:
    app = create_app(registry)
    business = make_business()
    client = client_for(app, registry, business, set())
    cases = endpoints()
    # A newly added route must be included in this permission sweep.
    assert len(cases) == sum(isinstance(c.original_route, APIRoute) for c in iter_route_contexts(api_router.routes))
    for method, path, body in cases:
        response = client.request(method, BASE + path, json=body)
        assert response.status_code == 403, (method, path, response.text)
    assistant = client_for(app, registry, business, {"ai.assistant.use"})
    assert assistant.get(BASE + "/agents/addressable").status_code == 200
    for path in ("/runs", "/usage", "/agents", "/agents/inventory/activity", f"/chains/{uuid.uuid4()}"):
        assert assistant.get(BASE + path).status_code == 403
    for method, path, body in cases:
        if path.startswith(("/conversations/", "/procurement/", "/campaigns/", "/autonomy/", "/customer-care/")):
            assert assistant.request(method, BASE + path, json=body).status_code == 403


def test_model_endpoints_reject_existing_foreign_business_ids(demo: Demo, registry: Registry) -> None:
    gw = FakeGateway([])
    set_router_for_tests(fake_router_by_class(agent=gw, writer=gw, fast_extract=gw))
    client = client_for(create_app(registry), registry, demo.other, ALL_PERMISSIONS)
    read_ctx = system_ctx(demo.business, ("purchasing.po.read",))
    with transaction(read_ctx) as db:
        request = purchasing.get_quote_request(db, read_ctx, demo.quote_request)
    cases = [
        (f"/conversations/{demo.conversations['Ada']}/summary", None),
        (f"/procurement/items/{demo.items['Cable set']}/request-quotes", None),
        (
            f"/procurement/quote-requests/{demo.quote_request}/extract",
            {"supplier_id": str(request.suppliers[0].supplier_id), "text": "USD 10.00, lead time 3 days."},
        ),
    ]
    for path, body in cases:
        response = client.post(BASE + path, json=body)
        assert response.status_code == 404, (path, response.text)
    assert gw.calls == []


def test_run_and_usage_apis_are_business_scoped_and_exclude_text(registry: Registry) -> None:
    own, other = make_business(), make_business(slug="other")
    a = fill_all_tables(system_ctx(own))
    b = fill_all_tables(system_ctx(other))
    client = client_for(create_app(registry), registry, own, {"ai.settings.read"})
    response = client.get(BASE + "/runs")
    assert response.status_code == 200
    assert [r["id"] for r in response.json()["items"]] == [str(a["run"])]
    assert str(b["run"]) not in response.text
    assert "Booked a brake service" not in response.text
    assert not ({"result", "prompt", "messages", "completion"} & response.json()["items"][0].keys())
    clock.set_clock(lambda: datetime(2026, 11, 3, 17, tzinfo=UTC))
    with transaction(system_ctx(other)) as db:
        db.execute(text("update ai_usage_daily set input_tokens=99999 where business_id=:b"), {"b": other})
    usage = client.get(BASE + "/usage").json()
    assert usage["today"]["input_tokens"] == 10
    assert usage["today"]["output_tokens"] == 5
    assert usage["days"][-1]["input_tokens"] == 10


def test_budget_at_100_percent_blocks_models_and_sets_banner_until_utc_reset(
    demo: Demo,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", 100)
    with transaction(system_ctx(demo.business)) as db:
        db.add(AiUsageDaily(business_id=demo.business, day=clock.now().date(), input_tokens=60, output_tokens=40))
    gw = FakeGateway([])
    set_router_for_tests(fake_router_by_class(agent=gw, writer=gw, fast_extract=gw, safety=gw))
    client = client_for(create_app(registry), registry, demo.business, ALL_PERMISSIONS)
    status = client.get(BASE + "/status").json()
    assert status == {
        "enabled": False, "reason": "ai.budget_exceeded", "policy": "hosted_vultr", "budget_percent": 100,
        "model_classes": {
            **{c: {"enabled": True, "reason": None} for c in ("agent", "writer", "fast_extract", "safety")},
            **{c: {"enabled": False, "reason": f"ai.not_configured: no usable route for {c}"}
               for c in ("grounding", "embed", "stt", "tts")},
        },
    }  # fmt: skip
    assert client.get(BASE + "/usage").json()["today"]["percent"] == 100
    read_ctx = system_ctx(demo.business, ("purchasing.po.read",))
    with transaction(read_ctx) as db:
        request = purchasing.get_quote_request(db, read_ctx, demo.quote_request)
    response = client.post(
        f"{BASE}/procurement/quote-requests/{demo.quote_request}/extract",
        json={"supplier_id": str(request.suppliers[0].supplier_id), "text": "USD 10.00, lead time 3 days."},
    )
    assert response.status_code == 429 and response.json()["code"] == "ai.budget_exceeded", response.text
    for method, path in (
        ("GET", "/briefing"),
        ("POST", f"/conversations/{demo.conversations['Ada']}/summary"),
        ("POST", f"/procurement/items/{demo.items['Cable set']}/request-quotes"),
    ):
        response = client.request(method, BASE + path)
        assert response.status_code == 429 and response.json()["code"] == "ai.budget_exceeded", response.text
    [event] = send(client, new_thread(client))
    assert event["error_code"] == "ai.budget_exceeded"
    _, message = inbound(demo.business)
    result = intake.run(message, demo.business)
    assert result.status == "budget_exceeded" and not result.proposal_ids
    assert gw.calls == []
    # Another business is unaffected; only the exhausted business resets with the UTC day.
    other = client_for(client.app, registry, demo.other, {"ai.assistant.use"})
    assert other.get(BASE + "/status").json()["enabled"] is True
    tomorrow = clock.now() + timedelta(days=1)
    clock.set_clock(lambda: tomorrow)
    assert client.get(BASE + "/status").json()["enabled"] is True


def hosted_router(gw: FakeGateway, *, baa: bool = False) -> Router:
    # The adapter is fake, but production-like route metadata must obey policy, with no key/network.
    return Router(
        RoutesFile(providers={"hosted": ProviderCfg(kind="openai_compat", vendor="vultr", hosted=True, baa=baa)},
                   routes={c: [RouteEntry(provider="hosted", model="offline")] for c in
                           ("agent", "writer", "fast_extract", "safety")}),
        {"hosted": gw},
    )  # fmt: skip


@pytest.mark.parametrize("baa", [False, True])
def test_regulated_profile_blocks_all_hosted_model_entrypoints_and_calls_no_model(
    demo: Demo,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
    baa: bool,
) -> None:
    monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    gw = FakeGateway([])
    set_router_for_tests(hosted_router(gw, baa=baa))
    client = client_for(create_app(registry), registry, demo.business, ALL_PERMISSIONS)
    status = client.get(BASE + "/status").json()
    assert status["enabled"] is False and status["reason"] == "ai.disabled_by_policy"
    assert status["policy"] == "regulated"
    read_ctx = system_ctx(demo.business, ("purchasing.po.read",))
    with transaction(read_ctx) as db:
        request = purchasing.get_quote_request(db, read_ctx, demo.quote_request)
    for method, path, body in [
        ("GET", "/briefing", None),
        ("POST", f"/conversations/{demo.conversations['Ada']}/summary", None),
        ("POST", f"/procurement/items/{demo.items['Cable set']}/request-quotes", None),
        (
            "POST",
            f"/procurement/quote-requests/{demo.quote_request}/extract",
            {"supplier_id": str(request.suppliers[0].supplier_id), "text": "USD 10.00, lead time 3 days."},
        ),
    ]:
        response = client.request(method, BASE + path, json=body)
        assert response.status_code == 409 and response.json()["code"] == "ai.disabled_by_policy", response.text
    [event] = send(client, new_thread(client))
    assert event["error_code"] == "ai.disabled_by_policy"
    _, message = inbound(demo.business)
    result = intake.run(message, demo.business)
    assert result.status == "policy_blocked" and result.error_code == "ai.disabled_by_policy"
    assert gw.calls == []
    # Metadata/owned-history endpoints remain readable; disabling inference does not revoke staff access.
    for path in ("/runs", "/usage", "/copilot/threads"):
        assert client.get(BASE + path).status_code == 200


def test_fake_provider_cannot_be_built_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "prod")
    routes = RoutesFile(
        providers={"dev": ProviderCfg(kind="fake", vendor="vultr")},
        routes={"agent": [RouteEntry(provider="dev", model="offline")]},
    )
    with pytest.raises(FakeProviderInProduction):
        build_adapters(routes)


def test_budget_checked_between_malicious_model_steps(demo: Demo, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.ai.agent.context import build_messages
    from app.modules.ai.agent.contexts import background_ctx_factory
    from app.modules.ai.agent.protocol import agent_step_json_schema
    from app.modules.ai.agent.runner import run_agent
    from app.modules.ai.gateway.types import ChatRequest
    from app.modules.ai.reservations import conservative_input
    from app.modules.ai.tests.helpers import step_tool
    from app.modules.ai.tests.test_safety_pass_structural import run_spec
    from app.modules.ai.tools.registry import tool_catalog

    scenario = run_spec("list_low_stock")
    first_request = ChatRequest(
        messages=build_messages(scenario.prompt, tool_catalog(scenario.tools), scenario),
        json_schema=agent_step_json_schema(),
    )
    # The first complete allowance fits; the subsequent tool exchange cannot reserve another one.
    monkeypatch.setattr(
        get_settings(),
        "ai_daily_token_budget_per_business",
        conservative_input(first_request) + first_request.max_output_tokens,
    )
    gw = FakeGateway([step_tool("list_low_stock"), step_final()])
    result = run_agent(background_ctx_factory(demo.business), scenario, router=fake_router_by_class(agent=gw))
    assert result.status == "budget_exceeded" and result.error_code == "ai.budget_exceeded"
    assert len(gw.calls) == 1 and not result.proposal_ids


@pytest.mark.parametrize("limit", [1, 2])
def test_repairs_consume_the_agent_step_limit(demo: Demo, owner_engine: Engine, limit: int) -> None:
    from app.modules.ai.agent.contexts import background_ctx_factory
    from app.modules.ai.agent.runner import run_agent
    from app.modules.ai.gateway.routes import Limits
    from app.modules.ai.tests.helpers import step_tool
    from app.modules.ai.tests.test_safety_pass_structural import run_spec

    gw = FakeGateway(["invalid JSON", step_tool("list_low_stock"), step_final()])
    router = fake_router_by_class(agent=gw)
    router.limits = Limits(max_steps=limit)
    result = run_agent(background_ctx_factory(demo.business), run_spec("list_low_stock"), router=router)
    assert result.status == "step_limit" and result.error_code == "ai.step_limit"
    assert len(gw.calls) == limit
    with owner_engine.connect() as db:
        assert db.scalar(text("select steps from ai_runs where id=:r"), {"r": result.run_id}) == limit


def test_repairs_consume_the_structured_step_limit(demo: Demo) -> None:
    from app.modules.ai.agent.contexts import background_ctx_factory
    from app.modules.ai.agent.prompts import load_prompt
    from app.modules.ai.errors import AiError
    from app.modules.ai.gateway.routes import Limits
    from app.modules.ai.schemas import ThreadSummary
    from app.modules.ai.structured import complete

    gw = FakeGateway(["invalid JSON", '{"summary":"Review","suggested_reply":"Please wait."}'])
    router = fake_router_by_class(fast_extract=gw)
    router.limits = Limits(max_steps=1)
    with pytest.raises(AiError) as exc:
        complete(
            background_ctx_factory(demo.business),
            use_case="summary",
            prompt=load_prompt("summary"),
            facts=[],
            output_model=ThreadSummary,
            trigger_type="api",
            trigger_key=f"safety:{uuid.uuid4()}",
            router=router,
        )
    assert exc.value.code == "ai.step_limit"
    assert len(gw.calls) == 1


@pytest.mark.parametrize("guard", ["regulated", "budget"])
@pytest.mark.parametrize("flow", ["reorder_note", "compare", "followup"])
def test_background_writers_and_procurement_obey_policy_and_budget(
    demo: Demo,
    monkeypatch: pytest.MonkeyPatch,
    guard: str,
    flow: str,
) -> None:
    from app.modules.ai.errors import AiError
    from app.modules.ai.usecases import procurement, reorder_note

    gw = FakeGateway([])
    if guard == "regulated":
        monkeypatch.setattr(get_settings(), "data_profile", "regulated")
        router = hosted_router(gw)
        expected = "ai.disabled_by_policy"
    else:
        monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", 0)
        router = fake_router_by_class(agent=gw, writer=gw, fast_extract=gw)
        expected = "ai.budget_exceeded"
    with pytest.raises(AiError) as exc:
        if flow == "reorder_note":
            reorder_note.run(demo.items["Cable set"], demo.business, router=router)
        elif flow == "compare":
            procurement.compare(demo.quote_request, demo.business, router=router)
        else:
            read_ctx = system_ctx(demo.business, ("purchasing.po.read",))
            with transaction(read_ctx) as db:
                [target] = purchasing.list_late_purchase_orders(db, read_ctx)
            procurement._followup(demo.business, target, router=router)
    assert exc.value.code == expected
    assert gw.calls == []
