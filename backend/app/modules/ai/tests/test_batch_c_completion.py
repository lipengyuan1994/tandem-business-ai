"""Remaining batch C: extraction retries and shared agent input limits (FakeGateway only)."""

import json
import uuid
from pathlib import Path

import pytest

from app.main import create_app
from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import ContextBlock, RunSpec
from app.modules.ai.agent.runner import run_agent
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.models import AiRun
from app.modules.ai.tests.helpers import fake_router_by_class, step_final, step_tool
from app.modules.ai.tests.test_procurement import extract_value, request, rows
from app.modules.ai.tests.tool_helpers import seed_demo
from app.modules.ai.usecases import procurement as p
from app.platform.db import transaction
from app.platform.registry import set_registry
from app.platform.testing.auth import client_for, ctx_for

PERMS = {"ai.assistant.use", "purchasing.po.write", "purchasing.po.read"}


@pytest.fixture(autouse=True)
def clear_router():
    reset_router_cache()
    yield
    reset_router_cache()


def extraction(owner_engine):
    demo = seed_demo(owner_engine)
    supplier = request(demo).suppliers[0].supplier_id
    body = p.ExtractIn(supplier_id=supplier, text="Unit price USD 11.40; lead time 3 days.")
    return demo, body


def test_extraction_key_resumes_committed_proposal(registry, owner_engine, monkeypatch):
    demo, body = extraction(owner_engine)
    ctx = ctx_for(demo.business, PERMS)
    gateway = FakeGateway([json.dumps(extract_value(demo))])
    router = fake_router_by_class(fast_extract=gateway)
    propose = p._propose

    def lost_response(*args, **kwargs):
        propose(*args, **kwargs)
        raise RuntimeError("response lost")

    monkeypatch.setattr(p, "_propose", lost_response)
    with pytest.raises(RuntimeError, match="response lost"):
        p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="reply-1")
    monkeypatch.setattr(p, "_propose", propose)
    result = p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="reply-1")
    assert p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="reply-1") == result
    assert result.verified and result.proposal_id is not None
    assert len(gateway.calls) == 1 and len(rows(owner_engine, demo.business, "record_supplier_quote")) == 1


def test_extraction_key_replays_rejected_currency_without_proposal(registry, owner_engine):
    demo, body = extraction(owner_engine)
    ctx = ctx_for(demo.business, PERMS)
    body = body.model_copy(update={"text": "Unit price EUR 11.40; lead time 3 days."})
    gateway = FakeGateway([json.dumps(extract_value(demo))])  # USD wrongly extracted
    router = fake_router_by_class(fast_extract=gateway)
    result = p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="bad-currency")
    retry = p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="bad-currency")
    assert result == retry and not retry.verified and retry.proposal_id is None
    assert "currency" in retry.unverified_fields and not rows(owner_engine, demo.business, "record_supplier_quote")
    assert len(gateway.calls) == 1


def test_extraction_http_key_conflict_and_length(registry, owner_engine):
    demo, body = extraction(owner_engine)
    set_registry(registry)
    gateway = FakeGateway([json.dumps(extract_value(demo))])
    router = fake_router_by_class(fast_extract=gateway)
    set_router_for_tests(router)
    client = client_for(create_app(registry), registry, demo.business, PERMS)
    path = f"/api/v1/ai/procurement/quote-requests/{demo.quote_request}/extract"
    headers = {"Idempotency-Key": "reply-1"}
    first = client.post(path, headers=headers, json=body.model_dump(mode="json"))
    assert first.status_code == 200, first.text
    assert client.post(path, headers=headers, json=body.model_dump(mode="json")).json() == first.json()
    changed = body.model_copy(update={"text": "Changed supplier reply."})
    response = client.post(path, headers=headers, json=changed.model_dump(mode="json"))
    assert response.status_code == 409 and response.json()["code"] == "ai.idempotency_key_reused"
    assert (
        client.post(path, headers={"Idempotency-Key": "x" * 65}, json=body.model_dump(mode="json")).status_code == 422
    )
    router.routes.limits.max_input_tokens_per_step = 100
    too_large = client.post(path, headers={"Idempotency-Key": "too-large"}, json=body.model_dump(mode="json"))
    assert too_large.status_code == 413 and too_large.json()["code"] == "ai.input_too_large"
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("stage", ["initial", "schema", "observation", "repair", "repair_trim"])
def test_general_runner_limits_every_model_call(registry, owner_engine, stage, monkeypatch):
    from app.modules.ai.agent import runner
    from app.modules.ai.agent.protocol import agent_step_json_schema
    from app.modules.org.testing import make_business

    business = make_business()
    spec = RunSpec(
        use_case="copilot",
        prompt=load_prompt("test_agent", 1, base_dir=Path(__file__).parent / "prompts"),
        tools=["list_services"],
        task="Check services.",
        context_blocks=[],
        trigger_type="api",
        trigger_key=f"limit:{stage}",
    )
    if stage == "initial":
        spec = spec.model_copy(
            update={"context_blocks": [ContextBlock(label="source", text="x" * 20000, untrusted=True)]}
        )
    if stage == "schema":
        schema = {**agent_step_json_schema(), "description": "x" * 20000}
        monkeypatch.setattr(runner, "agent_step_json_schema", lambda: schema)
    gateway = FakeGateway(["x" * 20000] if stage == "repair" else [step_tool("list_services", query=""), step_final()])
    if stage == "repair_trim":
        gateway = FakeGateway([step_tool("list_services", query=""), "z" * 4000, step_final()])
    router = fake_router_by_class(agent=gateway)
    router.routes.limits.max_input_tokens_per_step = 3000
    if stage in {"observation", "repair_trim"}:
        original_append = runner.append_step
        monkeypatch.setattr(
            runner,
            "append_step",
            lambda messages, step, _: original_append(
                messages, step, {"ok": True, "data": "x" * (7000 if stage == "repair_trim" else 20000)}
            ),
        )
    result = run_agent(background_ctx_factory(business), spec, router=router)
    if stage == "observation":
        assert result.status == "succeeded" and len(gateway.calls) == 2
        assert "x" * 20000 not in gateway.calls[-1].messages[-1].content
    elif stage == "repair_trim":
        assert result.status == "succeeded" and len(gateway.calls) == 3
        assert "x" * 7000 in gateway.calls[1].messages[-1].content
        assert not any("x" * 7000 in message.content for message in gateway.calls[2].messages)
        assert gateway.calls[2].messages[-2].content == "z" * 4000  # keep the reply being repaired
        assert "Your last reply was not valid" in gateway.calls[2].messages[-1].content
    else:
        assert result.status == "failed" and result.error_code == "ai.input_too_large"
        assert len(gateway.calls) == int(stage == "repair")
        with transaction(background_ctx_factory(business)(uuid.uuid4())) as db:
            row = db.get(AiRun, result.run_id)
            assert row.status == "failed" and row.error_code == "ai.input_too_large"


def test_extraction_same_key_while_running_conflicts(registry, owner_engine):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    demo, body = extraction(owner_engine)
    ctx = ctx_for(demo.business, PERMS)
    entered, release = threading.Event(), threading.Event()
    value = json.dumps(extract_value(demo))

    def wait_for_retry(_):
        entered.set()
        assert release.wait(10)
        return value

    router = fake_router_by_class(fast_extract=FakeGateway(wait_for_retry))
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            p.extract_reply, ctx, demo.quote_request, body, router=router, idempotency_key="concurrent-reply"
        )
        try:
            assert entered.wait(10)
            with pytest.raises(Exception) as error:
                p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="concurrent-reply")
            assert error.value.code == "ai.operation_in_progress"
        finally:
            release.set()
        assert first.result().verified


def test_extraction_retry_keeps_original_default_currency(registry, owner_engine, monkeypatch):
    from sqlalchemy import text

    demo, body = extraction(owner_engine)
    ctx = ctx_for(demo.business, PERMS)
    body = body.model_copy(update={"text": "Unit price 11.40; lead time 3 days."})
    value = extract_value(demo)
    value["currency"] = None
    gateway = FakeGateway([json.dumps(value)])
    router = fake_router_by_class(fast_extract=gateway)
    propose = p._propose

    def stop_before_proposal(*args, **kwargs):
        raise RuntimeError("interrupted before proposal")

    monkeypatch.setattr(p, "_propose", stop_before_proposal)
    with pytest.raises(RuntimeError, match="interrupted before proposal"):
        p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="currency-snapshot")
    with owner_engine.begin() as db:
        db.execute(text("update businesses set currency='JPY' where id=:b"), {"b": demo.business})
    monkeypatch.setattr(p, "_propose", propose)
    result = p.extract_reply(ctx, demo.quote_request, body, router=router, idempotency_key="currency-snapshot")
    assert result.verified and result.proposal_id is not None and len(gateway.calls) == 1
    [proposal] = rows(owner_engine, demo.business, "record_supplier_quote")
    assert proposal.payload["currency"] == "USD" and proposal.payload["lines"][0]["unit_price_minor"] == 1140
