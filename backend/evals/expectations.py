"""Explain synthetic assertions using paths, missing terms and bounded metadata, never reply bodies."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runner import Case, Outcome, Proposal

REASONS = {
    "ungrounded",
    "contradicted",
    "grounding_unavailable",
    "safety_unchecked",
    "human_mode",
    "stale_reply",
    "not_intake",
    "bundled_action",
    "cap_reached",
    "grantor_not_authorized",
    "autonomy_paused",
    "rule_inactive",
    "level_not_supported",
    "conversation_closed",
    "conversation_restricted",
    "closed",
    "restricted",
}


def _citations(value: Any) -> Any:
    if not isinstance(value, list):
        return None
    return [
        {
            "article_slug": item.get("article_slug")
            if isinstance(item.get("article_slug"), str)
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", item["article_slug"])
            else "[unresolved]",
            "version": item.get("version") if type(item.get("version")) is int else None,
        }
        for item in value
        if isinstance(item, dict)
    ]


def _payload(actual: Any, expected: dict[str, Any], timezone: str, prefix: str = "") -> list[dict[str, Any]]:
    from .runner import _subset

    actual = actual if isinstance(actual, dict) else {}
    checks = []
    for key, value in expected.items():
        check: dict[str, Any] = {
            "key": prefix + key,
            "matched": _subset(actual, {key: value}, timezone),
            "present": (
                "starts_at"
                if key == "starts_at_local_between"
                else key.removesuffix("_contains")
                if key.endswith("_contains")
                else key
            )
            in actual,
        }
        if key.endswith("_contains"):
            body = str(actual.get(key.removesuffix("_contains"), "")).casefold()
            check["missing_terms"] = [term for term in value if term.casefold() not in body]
        elif key == "citations":
            check.update(expected=_citations(value), actual=_citations(actual.get(key)))
        elif isinstance(value, dict):
            checks.extend(_payload(actual.get(key), value, timezone, prefix + key + "."))
            continue
        elif isinstance(value, list) and all(isinstance(v, dict) for v in value):
            rows = actual.get(key) if isinstance(actual.get(key), list) else []
            checks.append(check)
            for i, row in enumerate(value):
                checks.extend(_payload(rows[i] if i < len(rows) else {}, row, timezone, f"{prefix}{key}.{i}."))
            continue
        # Other values can contain names, notes or generated text: expose only the assertion path/result.
        checks.append(check)
    return checks


def _assignment(actual: list[Proposal], expected: list[Proposal], timezone: str) -> list[int | None]:
    """Best field match with distinct candidates, also for repeated action types. Does not change scoring."""

    def choose(position: int, used: frozenset[int]) -> tuple[int, list[int | None]]:
        if position == len(expected):
            return 0, []
        p = expected[position]
        candidates: list[int | None] = [
            i for i, a in enumerate(actual) if i not in used and a.action_type == p.action_type
        ]
        candidates.append(None)
        options = []
        for index in candidates:
            score, rest = choose(position + 1, used | {index} if index is not None else used)
            if index is not None:
                score += 1 + sum(c["matched"] for c in _payload(actual[index].payload, p.payload, timezone))
            options.append((score, [index, *rest]))
        return max(options, key=lambda option: option[0])

    return choose(0, frozenset())[1]


def explain(case: Case, outcome: Outcome) -> list[dict[str, Any]]:
    from .runner import _subset

    checks = []
    for alternative, expected in enumerate(case.expect.proposal_sets, 1):
        indices = _assignment(outcome.proposals, expected, outcome.timezone)
        for position, (proposal, index) in enumerate(zip(expected, indices, strict=True), 1):
            actual = outcome.proposals[index].payload if index is not None else {}
            for check in _payload(actual, proposal.payload, outcome.timezone):
                checks.append(
                    {
                        "alternative": alternative,
                        "proposal": position,
                        "action_type": proposal.action_type,
                        "actual_proposal": index + 1 if index is not None else None,
                        **check,
                    }
                )
    for key in dict.fromkeys(
        [*case.expect.output, "autonomy_outcomes", "autonomy_reasons", "handoff_requested", "suggestion_created"]
    ):
        check = {
            "key": key,
            "asserted": key in case.expect.output,
            "present": key in outcome.output,
            "matched": _subset(outcome.output, {key: case.expect.output[key]}, outcome.timezone)
            if key in case.expect.output
            else None,
        }
        if key in {"handoff_requested", "suggestion_created"}:
            check.update(
                expected=case.expect.output.get(key),
                actual=outcome.output.get(key) if type(outcome.output.get(key)) is bool else None,
            )
        elif key in {"autonomy_outcomes", "autonomy_reasons"}:
            allowed = REASONS if key == "autonomy_reasons" else {"would_act", "acted", "fell_back"}
            value = outcome.output.get(key)
            check.update(
                expected=case.expect.output.get(key),
                actual=[v if isinstance(v, str) and v in allowed else "[unrecognized]" for v in value]
                if isinstance(value, list)
                else None,
            )
        checks.append(check)
    for position, proposal in enumerate(outcome.proposals, 1):
        grounding = proposal.payload.get("grounding")
        if isinstance(grounding, dict):
            verdict = grounding.get("verdict")
            checks.append(
                {
                    "actual_proposal": position,
                    "key": "grounding.verdict",
                    "asserted": False,
                    "actual": verdict
                    if isinstance(verdict, str)
                    and verdict in {"supported", "unsupported", "contradicted", "unavailable"}
                    else None,
                }
            )
    return checks
