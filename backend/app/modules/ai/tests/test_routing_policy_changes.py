"""Fresh persisted business policy/profile at every attempt, repair and in-flight result boundary."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from app.modules.ai import structured
from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import RunSpec, StepEvent
from app.modules.ai.agent.runner import ModelCaller, StopRun, run_agent
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import Router
from app.modules.ai.gateway.routes import ModelClass, ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.gateway.types import ChatRequest, ProviderError
from app.modules.ai.models import AiRun
from app.modules.ai.tests.helpers import step_final, step_tool, system_ctx
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.db import transaction


@pytest.fixture(params=["business", "profile"])
def tightening(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> tuple[uuid.UUID, Callable[[], None]]:
    business = make_business()

    def tighten() -> None:
        if request.param == "profile":
            monkeypatch.setattr(get_settings(), "data_profile", "regulated")
        else:
            with transaction(system_ctx(business)) as db:
                db.execute(
                    text(
                        "update business_settings set settings = jsonb_set(settings, '{ai_policy}', "
                        "'\"strict_private\"'::jsonb) where business_id = :b"
                    ),
                    {"b": business},
                )

    return business, tighten


def routing(model_class: ModelClass, hosted: FakeGateway, local: FakeGateway | None) -> Router:
    hosted.name = "hosted"
    providers = {"hosted": ProviderCfg(kind="openai_compat", vendor="vultr", hosted=True, baa=True)}
    entries = [RouteEntry(provider="hosted", model="hosted")]
    adapters = {"hosted": hosted}
    if local is not None:
        local.name = "local"
        providers["local"] = ProviderCfg(
            kind="openai_compat", vendor="local", hosted=False, base_url="http://llm.internal:8080/v1"
        )
        entries.append(RouteEntry(provider="local", model="local"))
        adapters["local"] = local
    return Router(
        RoutesFile(providers=providers, routes={model_class: entries}), adapters, resolver=lambda _host: ["10.20.0.2"]
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("has_local", [False, True])
def test_policy_tightens_between_agent_steps(tightening: tuple[uuid.UUID, Callable[[], None]], has_local: bool) -> None:
    business, tighten = tightening
    hosted = FakeGateway([step_tool("list_services"), step_final("must not run")])
    local = FakeGateway([step_final("local result")]) if has_local else None
    router = routing("agent", hosted, local)

    def event(event: StepEvent) -> None:
        if event.kind == "step":
            tighten()

    result = run_agent(
        background_ctx_factory(business),
        RunSpec(
            use_case="copilot",
            prompt=load_prompt("copilot"),
            tools=["list_services"],
            task="List services",
            context_blocks=[],
            trigger_type="api",
            trigger_key=f"e0:{uuid.uuid4()}",
        ),
        router=router,
        on_event=event,
    )
    assert len(hosted.calls) == 1
    assert result.status == ("succeeded" if has_local else "policy_blocked")
    if local is not None:
        assert len(local.calls) == 1 and result.final is not None and result.final.message_to_staff == "local result"
    else:
        assert result.error_code == "ai.disabled_by_policy"
    with transaction(system_ctx(business)) as db:
        row = db.get(AiRun, result.run_id)
        assert row is not None and row.status == result.status


class Answer(BaseModel):
    value: str


@pytest.mark.parametrize("model_class", ["writer", "safety", "grounding"])
@pytest.mark.parametrize("has_local", [False, True])
def test_policy_tightens_before_structured_repair_including_safety_and_grounding(
    tightening: tuple[uuid.UUID, Callable[[], None]], model_class: ModelClass, has_local: bool
) -> None:
    business, tighten = tightening
    hosted = FakeGateway(['{"value":"old"}', '{"value":"must not run"}'])
    local = FakeGateway(['{"value":"local"}']) if has_local else None
    router = routing(model_class, hosted, local)
    prompt = load_prompt(
        "safety" if model_class == "safety" else "grounding" if model_class == "grounding" else "summary"
    ).model_copy(update={"model_class": model_class})

    def validate(answer: Answer) -> None:
        if answer.value == "old":
            tighten()
            raise ValueError("synthetic repair")

    def complete() -> Answer:
        return structured.complete(
            background_ctx_factory(business),
            use_case="safety" if model_class == "safety" else "grounding" if model_class == "grounding" else "summary",
            prompt=prompt,
            facts=[],
            output_model=Answer,
            trigger_type="api",
            trigger_key=f"e0:{uuid.uuid4()}",
            router=router,
            validate=validate,
        )[1]

    if has_local:
        assert complete().value == "local"
        assert local is not None and len(local.calls) == 1
    else:
        with pytest.raises(AiError) as exc:
            complete()
        assert exc.value.code == "ai.disabled_by_policy"
    assert len(hosted.calls) == 1


@pytest.mark.parametrize("model_class", ["agent", "safety", "grounding", "embed", "stt"])
@pytest.mark.parametrize("has_local", [False, True])
@pytest.mark.parametrize("outcome", ["timeout", "auth", "bad_request", "success"])
def test_fallback_rechecks_policy_and_discards_disallowed_inflight_results(
    tightening: tuple[uuid.UUID, Callable[[], None]], model_class: ModelClass, has_local: bool, outcome: str
) -> None:
    business, tighten = tightening

    def hosted_reply(_req: ChatRequest) -> str:
        tighten()
        if outcome != "success":
            raise ProviderError(outcome)  # type: ignore[arg-type]
        return '{"value":"must be discarded"}'

    hosted = FakeGateway(hosted_reply)
    local = FakeGateway(['{"value":"local"}']) if has_local else None
    router = routing(model_class, hosted, local)
    caller = ModelCaller(system_ctx(business), router, model_class, "hosted_vultr", "summary")
    if has_local:
        assert caller.call([], Answer.model_json_schema()).text == '{"value":"local"}'
        assert local is not None and len(local.calls) == 1
    else:
        with pytest.raises(StopRun) as exc:
            caller.call([], Answer.model_json_schema())
        assert (exc.value.status, exc.value.error_code) == ("policy_blocked", "ai.disabled_by_policy")
    assert len(hosted.calls) == 1


def test_policy_rechecked_after_budget_reservation(
    tightening: tuple[uuid.UUID, Callable[[], None]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.ai import reservations

    business, tighten = tightening
    original = reservations.reserve

    def reserve(*args: object, **kwargs: object) -> reservations.Reservation:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        tighten()
        return result

    monkeypatch.setattr(reservations, "reserve", reserve)
    hosted = FakeGateway(['{"value":"must not run"}'])
    local = FakeGateway(['{"value":"local"}'])
    caller = ModelCaller(system_ctx(business), routing("agent", hosted, local), "agent", "hosted_vultr", "summary")
    assert caller.call([], Answer.model_json_schema()).text == '{"value":"local"}'
    assert not hosted.calls and len(local.calls) == 1
