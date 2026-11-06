"""Precise synthetic expectation diagnostics without copying generated message text."""

from __future__ import annotations

import json
from pathlib import Path

from evals.runner import Case, Outcome, Proposal, load_cases, run_case, write_report


def test_field_diagnostics_explain_citations_terms_and_autonomy_without_body(tmp_path: Path) -> None:
    case = next(c for c in load_cases("intake") if c.id == "intake-kb-warranty")
    outcome = Outcome(
        status="succeeded",
        proposals=[
            Proposal(
                action_type="conversations.send_reply",
                payload={
                    "body": "30 days. PRIVATE_BODY private@example.com api_secret",
                    "citations": [{"article_slug": "repair-warranty", "version": 1, "article_id": "PRIVATE_ID"}],
                },
            )
        ],
        output={
            "handoff_requested": True,
            "suggestion_created": True,
            "autonomy_outcomes": ["fell_back"],
            "autonomy_reasons": ["ungrounded"],
        },
    )
    scored = run_case(case, lambda _: outcome)
    checks = scored.expectation_checks
    first = [c for c in checks if c.get("alternative") == 1]
    assert next(c for c in first if c["key"] == "body_contains")["missing_terms"] == ["thirty", "workmanship"]
    citation = next(c for c in first if c["key"] == "citations")
    assert citation["actual"] == [{"article_slug": "repair-warranty", "version": 1}]
    assert citation["expected"] == [{"article_slug": "repair-warranty", "version": 2}]
    assert not citation["matched"]
    assert next(c for c in checks if c["key"] == "autonomy_outcomes")["actual"] == ["fell_back"]
    assert next(c for c in checks if c["key"] == "autonomy_reasons")["actual"] == ["ungrounded"]
    assert next(c for c in checks if c["key"] == "handoff_requested")["actual"] is True
    report = write_report([scored], tmp_path, suite="intake", provider="fake", model="synthetic")
    saved = report.read_text() + report.with_suffix(".json").read_text()
    assert "missing_terms" in saved and "ungrounded" in saved and "repair-warranty" in saved
    for private in ("PRIVATE_BODY", "private@example.com", "api_secret", "PRIVATE_ID"):
        assert private not in saved
    assert json.loads(report.with_suffix(".json").read_text())["results"][0]["expectation_checks"] == checks


def test_matching_alternative_does_not_hide_output_failure() -> None:
    case = next(c for c in load_cases("intake") if c.id == "intake-kb-warranty")
    outcome = Outcome(
        status="succeeded",
        proposals=[
            Proposal(
                action_type="conversations.send_reply",
                payload={
                    "body": "Workmanship is covered for 30 days.",
                    "citations": [{"article_slug": "repair-warranty", "version": 2}],
                },
            )
        ],
        output={
            "handoff_requested": False,
            "suggestion_created": True,
            "autonomy_outcomes": ["fell_back"],
            "autonomy_reasons": ["ungrounded"],
        },
    )
    scored = run_case(case, lambda _: outcome)
    assert scored.matched_proposal_alternative == 2 and not scored.passed
    assert all(c["matched"] for c in scored.expectation_checks if c.get("alternative") == 2)
    assert not next(c for c in scored.expectation_checks if c["key"] == "suggestion_created")["matched"]


def test_diagnostics_pair_distinct_proposals_and_keep_missing_flags_unknown() -> None:
    base = next(c for c in load_cases("intake") if c.id == "intake-kb-warranty")
    expected = [
        {"action_type": "conversations.send_reply", "payload": {"body_contains": [term]}} for term in ("alpha", "beta")
    ]
    case = Case.model_validate({**base.model_dump(), "expect": {"proposals": expected}})
    actual = [Proposal(action_type="conversations.send_reply", payload={"body": term}) for term in ("beta", "alpha")]
    result = run_case(case, lambda _: Outcome(status="succeeded", proposals=actual))
    assert result.passed
    paired = [c["actual_proposal"] for c in result.expectation_checks if c["key"] == "body_contains"]
    assert paired == [2, 1]
    flag = next(c for c in result.expectation_checks if c["key"] == "handoff_requested")
    assert flag["present"] is False and flag["actual"] is None and flag["asserted"] is False
