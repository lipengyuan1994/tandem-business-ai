from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.ai.grounding import normalize
from app.modules.conversations.public import GroundingResult


def result(verdict: str, source: str = "db_tool", text: str = "Repairs cost 50.00 USD.") -> GroundingResult:
    return GroundingResult.model_validate(
        {"verdict": verdict, "claims": [{"text": text, "verdict": verdict, "source": source}]}
    )


@pytest.mark.parametrize("verdict", ["supported", "unsupported", "contradicted"])
def test_grounding_verdicts(verdict: str) -> None:
    value = normalize(result(verdict), "Repairs cost 50.00 USD.", [], {"list_services": "50.00 USD"})
    assert value.verdict == verdict


def test_number_mismatch_and_article_cannot_authorize_prices() -> None:
    value = normalize(result("supported"), "Repairs cost 50.00 USD.", [], {"list_services": "60.00 USD"})
    assert value.verdict == "contradicted"
    article = {"article_id": "article", "version": 1, "passages": ["Repairs cost 50.00 USD."]}
    value = normalize(result("supported", "article@1"), "Repairs cost 50.00 USD.", [article], {})
    assert value.verdict == "unsupported"


def test_no_claim_and_unreturned_source() -> None:
    value = GroundingResult(verdict="supported", claims=[])
    assert normalize(value, "Which service would you like?", [], {}).verdict == "supported"
    assert normalize(result("supported", "invented@1"), "Repairs cost 50.00 USD.", [], {}).verdict != "supported"


def test_claim_cap_and_conservative_overall() -> None:
    with pytest.raises(ValidationError):
        GroundingResult.model_validate({"verdict": "supported"})
    with pytest.raises(ValidationError):
        GroundingResult(verdict="supported", claims=result("supported").claims * 9)
    value = result("contradicted").model_copy(update={"verdict": "supported"})
    assert normalize(value, "Repairs cost 50.00 USD.", [], {"list_services": "50.00 USD"}).verdict == "contradicted"


def test_money_minor_units_are_converted_exactly() -> None:
    facts = {"list_services": [{"name": "Repairs", "price": {"minor": 5000, "currency": "USD"}}]}
    assert normalize(result("supported"), "Repairs cost 50.00 USD.", [], facts).verdict == "supported"
    reply = "Repairs cost 5000 USD."
    assert normalize(result("supported", text=reply), reply, [], facts).verdict == "contradicted"


def test_price_cannot_borrow_duration_or_another_service_price() -> None:
    facts = {
        "list_services": [
            {"name": "Brake service", "duration_minutes": 30, "price": {"minor": 6500, "currency": "USD"}},
            {"name": "Tyre service", "duration_minutes": 65, "price": {"minor": 3000, "currency": "USD"}},
        ]
    }
    body = "Brake service costs 30 USD."
    assert normalize(result("supported", text=body), body, [], facts).verdict == "contradicted"
    body = "Brake service takes 65 minutes."
    assert normalize(result("supported", text=body), body, [], facts).verdict == "contradicted"
    body = "Brake service costs 65.00 GBP."
    assert normalize(result("supported", text=body), body, [], facts).verdict == "contradicted"


def test_empty_database_cannot_authorize_a_qualitative_claim() -> None:
    body = "We insure meteor damage."
    assert normalize(result("supported", text=body), body, [], {}).verdict == "unsupported"


def test_stock_and_duration_cannot_borrow_reserved_or_buffer() -> None:
    facts = {
        "stock": [{"name": "Brake pads", "on_hand": 5, "reserved": 2, "available": 3}],
        "services": [{"name": "Brake service", "duration_minutes": 30, "buffer_minutes": 15}],
    }
    for body in ("We have 5 Brake pads available.", "Brake service takes 15 minutes."):
        assert normalize(result("supported", text=body), body, [], facts).verdict == "contradicted"
    for body in ("We have 3 Brake pads available.", "Brake service takes 30 minutes."):
        assert normalize(result("supported", text=body), body, [], facts).verdict == "supported"


def test_named_products_with_numbers_are_not_quantities_and_aliases_fail_closed() -> None:
    for name in ("Brake pads (pair)", "Inner tube 700c"):
        facts = {"stock": [{"item_name": name, "available": "5", "on_hand": "7", "reserved": "2"}]}
        body = f"{name}: 5 available."
        assert normalize(result("supported", text=body), body, [], facts).verdict == "supported"
    facts = {"services": [{"name": "Brake service", "price": "65 USD"}, {"name": "Flat repair", "price": "25 USD"}]}
    body = "Brakes cost 25 USD."
    assert normalize(result("supported", text=body), body, [], facts).verdict == "unsupported"


def test_no_claim_customer_questions_may_contain_numbers() -> None:
    value = GroundingResult(verdict="supported", claims=[])
    assert normalize(value, "Do you need one repair or two?", [], {}).verdict == "supported"


@pytest.mark.parametrize("period", ["30", "thirty"])
def test_policy_period_equivalent_spelling_is_supported(period: str) -> None:
    article = {"article_id": "policy", "version": 2, "passages": ["Repair workmanship is covered for thirty days."]}
    body = f"Repair workmanship is covered for {period} days."
    assert normalize(result("supported", "policy@2", body), body, [article], {}).verdict == "supported"


@pytest.mark.parametrize("period", ["3", "thirteen", "300", "thirty-one"])
def test_policy_period_mismatch_stays_unsupported(period: str) -> None:
    article = {"article_id": "policy", "version": 2, "passages": ["Repair workmanship is covered for thirty days."]}
    body = f"Repair workmanship is covered for {period} days."
    assert normalize(result("supported", "policy@2", body), body, [article], {}).verdict == "unsupported"


@pytest.mark.parametrize("code", ["ai.budget_exceeded", "ai.run_timeout", "ai.invalid_model_output"])
def test_check_errors_fail_closed(code: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.ai import grounding, structured
    from app.modules.ai.errors import AiError
    from app.platform.context import system_context

    def fail(*args: object, **kwargs: object) -> None:
        raise AiError(code)

    monkeypatch.setattr(structured, "complete", fail)
    value = grounding.check(lambda _: system_context(None), reply="Hello", articles=[], db_facts={}, trigger_key="test")
    assert value.verdict == "unavailable"
