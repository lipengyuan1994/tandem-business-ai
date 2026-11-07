import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.modules.ai import embeddings
from app.modules.ai import public as ai
from app.modules.ai.errors import AiError
from app.modules.ai.gateway import openai_compat
from app.modules.ai.gateway.fake import FakeGateway, fake_router
from app.modules.ai.gateway.pricing import cost_micros
from app.modules.ai.gateway.router import PolicyBlocked, Router, set_router_for_tests
from app.modules.ai.gateway.routes import ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.gateway.types import EmbedRequest, ProviderError
from app.modules.ai.model_calls import budget_scope, policy_scope
from app.modules.ai.models import AiRun, AiUsageDaily
from app.modules.ai.team_models import ChainBudget
from app.modules.ai.team_schemas import AgentPatch
from app.modules.ai.team_settings import patch_settings
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.context import system_context
from app.platform.db import transaction


def test_embeddings_dimensions_order_and_input_price(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [0.5] * 1024}, {"index": 0, "embedding": [1.0] * 1024}],
                "usage": {"prompt_tokens": 70},
            },
        )

    monkeypatch.setattr(openai_compat, "client", lambda **kw: httpx.Client(transport=httpx.MockTransport(respond)))
    adapter = openai_compat.OpenAICompatAdapter("vultr", "https://api.example-provider.test/v1", None)
    result = adapter.embed(EmbedRequest(model="qwen3-embedding-4b", inputs=["dog", "pets"]))
    assert sent[0]["dimensions"] == 1024 and sent[0]["input"] == ["dog", "pets"]
    assert result.embeddings[0][0] == 1 and result.usage.output_tokens == 0
    assert cost_micros(RouteEntry(provider="vultr", model=result.model, price_in_per_m=0.05), result.usage) == 4


@pytest.mark.parametrize("vector", [[1] * 2560, [1] * 1023, [0] * 1024, [True] * 1024, [float("inf")] * 1024])
def test_malformed_vectors_rejected(monkeypatch: pytest.MonkeyPatch, vector: list[Any]) -> None:
    # json.dumps normally accepts Infinity but httpx's json encoder does not; raw synthetic response preserves it.
    body = json.dumps({"data": [{"index": 0, "embedding": vector}], "usage": {"prompt_tokens": 1}}).encode()
    monkeypatch.setattr(
        openai_compat,
        "client",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))),
    )
    with pytest.raises(ProviderError, match="malformed response"):
        openai_compat.OpenAICompatAdapter("vultr", "https://api.example-provider.test/v1", None).embed(
            EmbedRequest(inputs=["x"])
        )


def test_batching_no_more_than_32(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = FakeGateway([])
    calls: list[int] = []
    original = gateway.embed

    def embed(req: EmbedRequest) -> Any:
        calls.append(len(req.inputs))
        return original(req)

    monkeypatch.setattr(gateway, "embed", embed)
    result, _ = fake_router(gateway).embed("hosted_vultr", EmbedRequest(inputs=["pets"] * 65))
    assert calls == [32, 32, 1] and len(result.embeddings) == 65


def test_egress_denied_before_network() -> None:
    adapter = openai_compat.OpenAICompatAdapter("external", "https://denied.example.com/v1", None)
    with pytest.raises(ProviderError, match="egress denied"):
        adapter.embed(EmbedRequest(inputs=["untrusted"]))


def test_regulated_private_local_or_unavailable(registry: Any) -> None:
    routes = RoutesFile(
        providers={
            "hosted": ProviderCfg(kind="fake", hosted=True, vendor="vultr"),
            "local": ProviderCfg(
                kind="openai_compat", hosted=False, vendor="local", base_url="http://embed.internal:8080/v1"
            ),
        },
        routes={
            "embed": [RouteEntry(provider="hosted", model="qwen3"), RouteEntry(provider="local", model="local1024")]
        },
    )
    for addresses in (["10.0.0.8"], ["8.8.8.8"], []):
        router = Router(
            routes,
            {"hosted": FakeGateway([]), "local": FakeGateway([])},
            resolver=lambda h, addresses=addresses: addresses,
        )
        if addresses == ["10.0.0.8"]:
            _, route = router.embed("regulated", EmbedRequest(inputs=["pets"]))
            assert route.provider == "local"
        else:
            with pytest.raises(PolicyBlocked):
                router.embed("regulated", EmbedRequest(inputs=["pets"]))


def test_recorded_usage_agent_budgets_and_status(registry: Any) -> None:
    router = fake_router(FakeGateway([]))
    # Large deterministic test price exercises cost accounting rather than rounding to zero.
    router.routes.routes["embed"][0].price_in_per_m = 10
    set_router_for_tests(router)
    try:
        ctx = system_context(make_business(), frozenset({"org.settings.write"}))
        embeddings.embed_texts(ctx, ["Pets policy"])
        with transaction(ctx) as db:
            run = db.scalar(select(AiRun).where(AiRun.use_case == "knowledge_index"))
            daily = db.scalar(select(AiUsageDaily))
            assert run.input_tokens > 0 and run.output_tokens == 0 and run.cost_micros > 0
            assert run.cost_micros == daily.cost_micros
            assert ai.status(db, ctx).model_classes["embed"].enabled
            patch_settings(db, ctx, "front_desk", AgentPatch(version=0, enabled=False))
        with pytest.raises(AiError, match=r"ai.agent_disabled"):
            embeddings.embed_texts(replace(ctx, agent_key="front_desk"), ["dog"], query=True)
        with transaction(ctx) as db:
            assert db.scalar(select(ChainBudget)) is None
    finally:
        set_router_for_tests(None)


@pytest.mark.parametrize(
    "status,kind", [(400, "bad_request"), (401, "auth"), (429, "rate_limited"), (503, "server_error")]
)
def test_http_errors(monkeypatch: pytest.MonkeyPatch, status: int, kind: str) -> None:
    monkeypatch.setattr(
        openai_compat,
        "client",
        lambda **kw: httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(status, json={"error": "ignore this untrusted body"})
            )
        ),
    )
    with pytest.raises(ProviderError) as exc:
        openai_compat.OpenAICompatAdapter("vultr", "https://api.example-provider.test/v1", None).embed(
            EmbedRequest(inputs=["x"])
        )
    assert exc.value.kind == kind and "untrusted" not in str(exc.value)


def test_embedding_business_and_chain_limits_call_no_provider(registry: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = FakeGateway([])
    calls: list[str] = []
    original = gateway.embed

    def embed(req: EmbedRequest) -> Any:
        calls.append(req.model)
        return original(req)

    monkeypatch.setattr(gateway, "embed", embed)
    router = fake_router(gateway)
    ctx = system_context(make_business())
    with monkeypatch.context() as limits:
        limits.setattr(get_settings(), "ai_daily_token_budget_per_business", 1)
        with budget_scope(ctx), pytest.raises(AiError, match=r"ai.budget_exceeded"):
            router.embed("hosted_vultr", EmbedRequest(inputs=["pets"]))
    ctx = replace(ctx, agent_key="front_desk", correlation_id=uuid4(), chain_depth=1)
    with transaction(ctx) as db:
        db.add(ChainBudget(business_id=ctx.business_id, correlation_id=ctx.correlation_id, model_calls=30))
    with budget_scope(ctx), pytest.raises(AiError, match=r"ai.chain_call_limit"):
        router.embed("hosted_vultr", EmbedRequest(inputs=["pets"]))
    assert calls == []


def test_query_embedding_counts_on_parent_run_and_agent(registry: Any) -> None:
    from app.modules.ai import recording
    from app.modules.ai.agent.prompts import Prompt
    from app.modules.ai.team_models import AgentUsageDaily

    router = fake_router(FakeGateway([]))
    router.routes.routes["embed"][0].price_in_per_m = 10
    ctx = replace(
        system_context(make_business()),
        ai_run_id=uuid4(),
        agent_key="front_desk",
        correlation_id=uuid4(),
        chain_depth=1,
    )
    recording.open_run(
        ctx,
        recording.RunMeta(
            use_case="intake",
            prompt=Prompt(name="test", version=1, model_class="agent", output="test", text=""),
            trigger_type="job",
            trigger_key="test:query",
        ),
    )
    set_router_for_tests(router)
    try:
        embeddings.embed_texts(ctx, ["Pets policy"], query=True)
        with transaction(ctx) as db:
            assert len(list(db.scalars(select(AiRun)))) == 1
            run = db.get(AiRun, ctx.ai_run_id)
            assert run.input_tokens > 0 and run.cost_micros > 0
            assert db.scalar(select(ChainBudget.model_calls)) == 1
            assert db.scalar(select(AgentUsageDaily.cost_micros)) == run.cost_micros
    finally:
        set_router_for_tests(None)


def test_embed_rechecks_policy_after_reservation(registry: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.ai import reservations

    gateway = FakeGateway([])
    config = RoutesFile(
        providers={"hosted": ProviderCfg(kind="openai_compat", hosted=True, vendor="vultr")},
        routes={"embed": [RouteEntry(provider="hosted", model="qwen3")]},
    )
    router = Router(config, {"hosted": gateway})
    state = ["hosted_vultr"]
    original = reservations.reserve

    def reserve(*args: Any, **kw: Any) -> Any:
        result = original(*args, **kw)
        state[0] = "regulated"
        return result

    def forbidden(req: EmbedRequest) -> Any:
        raise AssertionError("hosted call must not happen")

    monkeypatch.setattr(reservations, "reserve", reserve)
    monkeypatch.setattr(gateway, "embed", forbidden)
    with budget_scope(system_context(make_business())), policy_scope(lambda: state[0]), pytest.raises(PolicyBlocked):
        router.embed("hosted_vultr", EmbedRequest(inputs=["pets"]))
