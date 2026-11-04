"""AI-05: structured completion (contracts §9) and the run/usage API (§14)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.main import create_app
from app.modules.ai.agent.contexts import background_ctx_factory
from app.modules.ai.agent.prompts import load_prompt
from app.modules.ai.agent.protocol import ContextBlock
from app.modules.ai.errors import AiError
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import reset_router_cache, set_router_for_tests
from app.modules.ai.gateway.types import ProviderError
from app.modules.ai.models import AiRun, AiRunStep
from app.modules.ai.schemas import RunOut, ThreadSummary
from app.modules.ai.structured import complete
from app.modules.ai.tests.helpers import fake_router_by_class, insert_run, system_ctx
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.db import transaction
from app.platform.registry import Registry
from app.platform.testing.auth import client_for

PROMPTS = Path(__file__).parent / "prompts"
SUMMARY = ThreadSummary(summary="Ada asks about brakes.", open_questions=[], suggested_reply="We can help on Tuesday.")


@pytest.fixture(autouse=True)
def _clean_router() -> Iterator[None]:
    set_router_for_tests(None)
    reset_router_cache()
    yield
    set_router_for_tests(None)
    reset_router_cache()


def summarize(biz: uuid.UUID, gw: FakeGateway, **kw: Any) -> tuple[uuid.UUID, ThreadSummary]:
    return complete(
        background_ctx_factory(biz),
        use_case="summary",
        prompt=load_prompt("test_structured", base_dir=PROMPTS),
        facts=[
            ContextBlock(label="conversation", source_id=uuid.uuid4(), text="Ada: my brakes squeal", untrusted=True)
        ],
        output_model=ThreadSummary,
        trigger_type="api",
        trigger_key=f"summary:{uuid.uuid4()}",
        router=kw.pop("router", None) or fake_router_by_class(fast_extract=gw),
        **kw,
    )


def run_and_steps(biz: uuid.UUID, run_id: uuid.UUID) -> tuple[AiRun, list[AiRunStep]]:
    with transaction(system_ctx(biz)) as db:
        row = db.get(AiRun, run_id)
        steps = list(db.scalars(select(AiRunStep).where(AiRunStep.run_id == run_id).order_by(AiRunStep.step_no)))
        assert row is not None
        db.expunge_all()
        return row, steps


def test_structured_complete_validates_and_repairs_on_validate_error() -> None:
    biz = make_business()
    gw = FakeGateway([SUMMARY.model_dump_json()])
    run_id, value = summarize(biz, gw)
    assert value == SUMMARY
    row, steps = run_and_steps(biz, run_id)
    assert (row.status, row.use_case, row.model_class, row.steps) == ("succeeded", "summary", "fast_extract", 1)
    assert [s.kind for s in steps] == ["completion"]
    assert row.result == SUMMARY.model_dump(mode="json")
    request = gw.calls[0]
    assert request.json_schema == ThreadSummary.model_json_schema() and request.use_case == "summary"
    assert request.messages[0].content == load_prompt("test_structured", base_dir=PROMPTS).text
    assert request.messages[1].content.startswith('<untrusted source="conversation"')

    # validate() raising ValueError (e.g. an invented number) triggers the single repair.
    invented = SUMMARY.model_copy(update={"suggested_reply": "That costs $999."})

    def no_invented_prices(s: ThreadSummary) -> None:
        if "$" in s.suggested_reply:
            raise ValueError("suggested_reply mentions a price that is not in the facts")

    gw = FakeGateway([invented.model_dump_json(), SUMMARY.model_dump_json()])
    run_id, value = summarize(biz, gw, validate=no_invented_prices)
    assert value == SUMMARY
    repair = gw.calls[1].messages[-1].content
    assert repair.startswith("Your last reply was not valid: suggested_reply mentions a price")
    _, steps = run_and_steps(biz, run_id)
    assert [(s.kind, s.error_code) for s in steps] == [("repair", "ai.invalid_model_output"), ("completion", None)]

    # Still invalid after the repair: AiError, run failed.
    gw = FakeGateway(["not json", json.dumps({"summary": "x"})])
    with pytest.raises(AiError) as err:
        summarize(biz, gw)
    assert err.value.code == "ai.invalid_model_output" and len(gw.calls) == 2


def test_structured_errors_map_to_ai_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    biz = make_business()
    with pytest.raises(AiError) as err:
        summarize(biz, FakeGateway([ProviderError("timeout")]))
    assert err.value.code == "ai.provider_unavailable" and err.value.status == 503
    with pytest.raises(AiError) as err:
        summarize(biz, FakeGateway([]), router=fake_router_by_class(agent=FakeGateway([])))  # no fast_extract route
    assert err.value.code == "ai.not_configured"
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", 0)
    gw = FakeGateway([SUMMARY.model_dump_json()])
    with pytest.raises(AiError) as err:
        summarize(biz, gw)
    assert err.value.code == "ai.budget_exceeded" and gw.calls == []
    with transaction(system_ctx(biz)) as db:
        statuses = sorted(r.status for r in db.scalars(select(AiRun)))
    assert statuses == ["budget_exceeded", "failed", "policy_blocked"]


# ------------------------------------------------------------------------------------------- API


def test_runs_and_usage_endpoints_permissions_and_no_text(registry: Registry) -> None:
    biz = make_business()
    _, _ = summarize(biz, FakeGateway([SUMMARY.model_dump_json()]))
    # Inserted directly (not through open_run), so it does not count in today's usage.
    insert_run(system_ctx(biz), status="failed", error_code="ai.provider_unavailable", result={"text": "secret"})
    other = make_business(slug="other-shop")
    insert_run(system_ctx(other))
    app = create_app(registry)

    staff = client_for(app, registry, biz, {"ai.assistant.use"})
    assert staff.get("/api/v1/ai/runs").status_code == 403
    assert staff.get("/api/v1/ai/usage").status_code == 403

    admin = client_for(app, registry, biz, {"ai.settings.read"})
    r = admin.get("/api/v1/ai/runs")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2  # the other business's run is not visible
    assert all(set(item) == set(RunOut.model_fields) for item in items)
    assert "secret" not in r.text and "Ada" not in r.text and "result" not in items[0]
    assert {i["use_case"] for i in items} == {"summary", "intake"}

    r = admin.get("/api/v1/ai/runs", params={"status": "failed"})
    assert [i["error_code"] for i in r.json()["items"]] == ["ai.provider_unavailable"]
    assert admin.get("/api/v1/ai/runs", params={"use_case": "summary"}).json()["items"][0]["status"] == "succeeded"
    page = admin.get("/api/v1/ai/runs", params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    rest = admin.get("/api/v1/ai/runs", params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert rest["items"][0]["id"] != page["items"][0]["id"] and rest["next_cursor"] is None
    assert admin.get("/api/v1/ai/runs", params={"status": "nope"}).status_code == 422

    r = admin.get("/api/v1/ai/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["today"]["input_tokens"] > 0 and body["today"]["budget_tokens"] == 2_000_000
    assert len(body["days"]) == 30 and body["days"][-1]["runs"] == 1 and body["days"][0]["runs"] == 0
    assert body["days"][-1]["input_tokens"] == body["today"]["input_tokens"]


def test_status_reports_budget(monkeypatch: pytest.MonkeyPatch, registry: Registry) -> None:
    biz = make_business()
    set_router_for_tests(fake_router_by_class(agent=FakeGateway([])))
    client = client_for(create_app(registry), registry, biz, {"ai.assistant.use"})
    assert client.get("/api/v1/ai/status").json() == {
        "enabled": True, "reason": None, "policy": "hosted_vultr", "budget_percent": 0,
    }  # fmt: skip
    monkeypatch.setattr(get_settings(), "ai_daily_token_budget_per_business", 0)
    body = client.get("/api/v1/ai/status").json()
    assert (body["enabled"], body["reason"], body["budget_percent"]) == (False, "ai.budget_exceeded", 100)
