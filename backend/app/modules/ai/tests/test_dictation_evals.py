import json

from app.modules.ai.dictation import polish
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import set_router_for_tests
from app.modules.ai.tests.helpers import fake_router_by_class
from app.modules.org.testing import make_business
from app.platform.testing.auth import ctx_for
from evals.runner import Outcome, load_cases, run_case, summarize
from evals.runtime import validate_cases


def test_dictation_cases_offline_prompt_guards_scoring_and_thresholds():
    cases = load_cases("dictation")
    assert len(cases) >= 20
    validate_cases(cases)
    ctx = ctx_for(make_business(), {"ai.assistant.use"})
    rows = []
    try:
        for case in cases:
            expected = case.input["expected_text"]
            gateway = FakeGateway([json.dumps({"text": expected})])
            router = fake_router_by_class(writer=gateway)
            output, polished, warnings = polish(ctx, case.input["message"], case.input["dictionary"], router)
            assert polished, (case.id, warnings)
            outcome = Outcome(
                status="succeeded",
                text=output,
                output={"text": output, "polished": polished, "warnings": warnings},
                steps=1,
            )
            scored = run_case(case, lambda _, outcome=outcome: outcome)
            assert scored.passed, (case.id, scored.failures)
            assert gateway.calls[0].json_mode == "json_object"
            rows.append(scored)
        result = summarize(rows)
        assert result["thresholds_met"] and result["pass_rate"] == 1
        assert result["guard_fallback_cases"] == 0
        bad = rows[0].model_copy(update={"passed": False, "guard_fallback": True})
        assert summarize([bad])["guard_fallback_cases"] == 1
        assert not summarize([bad])["thresholds_met"]
    finally:
        set_router_for_tests(None)


def test_dictation_eval_runtime_uses_production_polish_and_metadata_only(owner_engine):
    from evals.runtime import LiveRuntime

    case = load_cases("dictation")[0]
    gateway = FakeGateway([json.dumps({"text": case.input["expected_text"]})])
    runtime = LiveRuntime("dictation", router=fake_router_by_class(writer=gateway), owner_engine=owner_engine)
    try:
        outcome = runtime.execute(case)
        assert run_case(case, lambda _: outcome).passed, outcome.model_dump()
        assert outcome.output["polished"]
        assert len(outcome.provenance["runs"]) == 1
        assert outcome.provenance["result_keys"] == ["polished"]
    finally:
        runtime.close()
        set_router_for_tests(None)
