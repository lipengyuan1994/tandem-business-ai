"""Run the five C1 intake eval cases through the real harness with fake inference only."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, text

from app.modules.ai.gateway.fake import FakeGateway, fake_router
from app.modules.ai.gateway.types import ChatRequest
from app.modules.ai.tests.helpers import step_final, step_tool
from app.modules.ai.tests.test_intake_agent import observation
from evals.runner import load_cases, run_case
from evals.runtime import LiveRuntime


@pytest.mark.parametrize("slug", ["kb-warranty", "kb-returns", "kb-unknown", "kb-price", "kb-injection"])
@pytest.mark.parametrize("period", ["thirty", "30"])
def test_c1_eval_fake_harness(slug: str, period: str, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    case = next(c for c in load_cases("intake") if c.id == "intake-" + slug)
    turns = 0

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("External calls are forbidden in this fake harness test")

    monkeypatch.setattr(httpx.Client, "send", no_network)

    def reply(req: ChatRequest) -> str:
        nonlocal turns
        if req.use_case == "safety":
            return json.dumps({"flagged": False, "category": "none"})
        with owner_engine.connect() as db:
            conv = db.scalar(text("select id from conversations where subject=:s"), {"s": case.id})
        turn = turns
        turns += 1
        if slug == "kb-price":
            # The price is in the trusted catalogue context, never the hostile article.
            if turn == 0:
                assert "6500" in req.messages[-1].content
                return step_tool("propose_reply", conversation_id=conv, text="Brake service costs $65.00.")
            return step_final("Prepared the current catalogue price.")
        if turn == 0:
            return step_tool(
                "search_knowledge",
                query="meteor insurance" if slug == "kb-unknown" else "returns" if slug == "kb-returns" else "warranty",
            )
        if turn == 1:
            articles = observation(req)["data"]["articles"]
            if slug == "kb-unknown":
                assert articles == []
                return step_tool(
                    "propose_reply", conversation_id=conv, text="A person will follow up here.", knowledge_missing=True
                )
            a = articles[0]
            assert a["version"] == 2
            if slug == "kb-injection":
                assert "50% off" in req.messages[-1].content
                assert req.messages[-1].content.count("</untrusted>") == 1
                assert "50% off" not in req.messages[0].content
            body = (
                f"Return unused accessories with a receipt within {period} days."
                if slug == "kb-returns"
                else f"Repair workmanship is covered for {period} days."
            )
            return step_tool(
                "propose_reply",
                conversation_id=conv,
                text=body,
                citations=[a["article_id"]],
            )
        return step_final("Prepared a cited knowledge answer.")

    gateway = FakeGateway(reply)

    def check(req: ChatRequest) -> str:
        if slug == "kb-price":
            claim, source = "Brake service costs $65.00.", "db_tool"
        else:
            # Citation references are copied from the exact checker input, not a separate knowledge lookup.
            import re

            content = req.messages[1].content
            article = re.search(r'"article_id": "([^"]+)"', content)
            version = re.search(r'"version": (\d+)', content)
            assert article and version
            source = f"{article[1]}@{version[1]}"
            claim = (
                f"Return unused accessories with a receipt within {period} days."
                if slug == "kb-returns"
                else f"Repair workmanship is covered for {period} days."
            )
        return json.dumps(
            {"claims": [{"text": claim, "verdict": "supported", "source": source}], "verdict": "supported"}
        )

    runtime = LiveRuntime(
        "intake", router=fake_router(gateway, grounding=FakeGateway(check)), owner_engine=owner_engine
    )
    scored = run_case(case, runtime.execute)
    assert scored.passed, scored
    assert scored.provenance["proposal_statuses"] == (["cancelled"] if slug == "kb-unknown" else ["pending_review"])
    assert gateway.calls
