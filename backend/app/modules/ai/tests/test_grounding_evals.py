"""Labelled grounding suite through the real harness. Scripted outputs test wiring, not live accuracy."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.tests.helpers import fake_router_by_class
from evals.runner import Case, Outcome, load_cases, run_case, summarize
from evals.runtime import LiveRuntime, validate_cases


def test_grounding_suite_fake_harness(owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_external(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("External calls forbidden")

    monkeypatch.setattr(httpx.Client, "send", no_external)
    cases = load_cases("grounding")
    validate_cases(cases)
    rows = []
    for case in cases:

        def reply(request: ChatRequest, case: Case = case) -> str:
            assert request.use_case == "grounding" and request.json_mode == "json_object"
            assert request.max_output_tokens <= 800
            assert case.input["message"] in request.messages[1].content
            assert "<untrusted" in request.messages[1].content
            verdict = case.expect.output["verdict"]
            no_claim = case.id.endswith("no-claim")
            articles = case.input["articles"]
            source = f"{articles[0]['article_id']}@{articles[0]['version']}" if articles else "db_tool"
            return json.dumps(
                {
                    "verdict": verdict,
                    "claims": []
                    if no_claim
                    else [
                        {
                            "text": case.input["message"][:160],
                            "verdict": verdict,
                            "source": source if verdict == "supported" else "none",
                        }
                    ],
                }
            )

        runtime = LiveRuntime(
            "grounding", router=fake_router_by_class(grounding=FakeGateway(reply)), owner_engine=owner_engine
        )
        scored = run_case(case, runtime.execute)
        assert scored.passed, scored
        rows.append(scored)
    metrics = summarize(rows)
    assert metrics["unsupported_recall"] == 1 and metrics["unsupported_precision"] == 1
    assert metrics["thresholds_met"]


def test_grounding_metrics_do_not_count_unavailable_or_failed_as_recalled() -> None:
    cases = load_cases("grounding")
    rows = [run_case(c, lambda _: Outcome(status="succeeded", output={"verdict": "supported"})) for c in cases]
    assert summarize(rows)["unsupported_recall"] == 0
    assert not summarize(rows)["thresholds_met"]

    def fail(_: Any) -> Any:
        raise RuntimeError("synthetic failure")

    rows = [run_case(c, fail) for c in cases]
    assert summarize(rows)["unsupported_recall"] == 0


def test_false_positives_lower_precision() -> None:
    rows = [
        run_case(c, lambda _: Outcome(status="succeeded", output={"verdict": "unsupported"}))
        for c in load_cases("grounding")
    ]
    assert summarize(rows)["unsupported_precision"] == 0.7
    assert not summarize(rows)["thresholds_met"]
