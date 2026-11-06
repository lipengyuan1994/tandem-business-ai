from typing import Any

import pytest

from app.modules.ai.gateway.fake import FakeGateway, fake_router
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.org.testing import make_business
from app.platform.context import system_context
from evals.retrieval import evaluate
from evals.runner import Outcome, load_cases, run_case, summarize
from evals.runtime import validate_cases


@pytest.mark.parametrize("case", load_cases("retrieval"), ids=lambda c: c.id)
def test_paraphrase_citation_eval(case: Any, registry: Any) -> None:
    validate_cases([case])
    set_router_for_tests(fake_router(FakeGateway([])))
    try:
        ctx = system_context(make_business(), frozenset({"org.settings.write", "conversations.thread.read"}))
        output = evaluate(ctx, case.input)
        scored = run_case(case, lambda _: Outcome(status="succeeded", output=output))
        assert scored.passed, scored.failures
        assert summarize([scored])["citation_accuracy"] == 1
    finally:
        set_router_for_tests(None)
