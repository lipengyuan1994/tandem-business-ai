"""Independent, bounded claim checks. Called before opening the proposal transaction."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from app.modules.conversations.public import GroundingResult

from .agent.contexts import CtxFactory
from .agent.prompts import load_prompt
from .agent.protocol import ContextBlock
from .errors import AiError
from .gateway.router import Router
from .grounding_numbers import MissingFact, policy_number_text, validate_database_numbers
from .usecases.briefing import validate_numbers

# These facts may never be authorized by an owner-written article, even a cited one.
_OPERATIONAL = re.compile(
    r"\b(?:price|costs?|USD|GBP|EUR|hours?|opens?|closes?|stock|available|duration|minutes?|takes?)\b|[$£€]", re.I
)


def database_values(value: Any) -> Any:
    """Render integer minor-unit money exactly, retaining both fixed and canonical decimal spellings.

    Database tools intentionally return minor units. 6500 minor units must support 65.00, never 6500 dollars.
    This derived representation is supplied to the checker too; it is not a new lookup or model assertion.
    """
    if isinstance(value, list):
        return [database_values(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {
        k: database_values(v) for k, v in value.items() if not k.endswith("_id") and k not in {"minor", "price_minor"}
    }
    minor = value.get("minor", value.get("price_minor"))
    if isinstance(minor, int) and value.get("currency"):
        amount = Decimal(minor) / 100
        out["amount"] = format(amount, ".2f")
        out["amount_canonical"] = format(amount.normalize(), "f")
    return out


def normalize(
    value: GroundingResult, reply: str, articles: list[dict[str, Any]], db_facts: dict[str, Any]
) -> GroundingResult:
    """Source membership and exact numeric tokens are code checks; semantic support remains measured inference."""
    value = value.model_copy(deep=True)
    sources = {f"{a['article_id']}@{a['version']}": " ".join(a["passages"]) for a in articles}
    facts = json.dumps(database_values(db_facts), default=str)
    for claim in value.claims:
        source = facts if claim.source == "db_tool" else sources.get(claim.source)
        if claim.verdict != "supported":
            continue
        if not source or claim.source == "none" or (claim.source == "db_tool" and not db_facts):
            claim.verdict = "unsupported"
            continue
        if _OPERATIONAL.search(claim.text) and claim.source != "db_tool":
            claim.verdict = "unsupported"
            continue
        try:
            if claim.source == "db_tool":
                validate_database_numbers(claim.text, db_facts)
                validate_numbers(claim.text, source)
            else:
                validate_numbers(policy_number_text(claim.text), policy_number_text(source))
        except MissingFact:
            claim.verdict = "unsupported"
        except ValueError:
            claim.verdict = "contradicted" if claim.source == "db_tool" and db_facts else "unsupported"
    verdicts = {c.verdict for c in value.claims} | {value.verdict}
    # Check unclaimed assertions too, while excluding questions back to the customer from factual numbers.
    assertions = " ".join(part for part in re.split(r"(?<=[.!?])\s+", reply) if not part.rstrip().endswith("?"))
    try:
        if not value.claims and assertions and _OPERATIONAL.search(assertions):
            validate_database_numbers(assertions, db_facts)
        include_articles = bool(value.claims) or not _OPERATIONAL.search(assertions)
        evidence = facts + " ".join(sources.values()) if include_articles else facts
        if include_articles and sources:
            validate_numbers(policy_number_text(assertions), policy_number_text(evidence))
        else:
            validate_numbers(assertions, evidence)
    except MissingFact:
        verdicts.add("unsupported")
    except ValueError:
        verdicts.add("contradicted" if db_facts else "unsupported")
    value.verdict = next(v for v in ("unavailable", "contradicted", "unsupported", "supported") if v in verdicts)
    return value


def check(
    factory: CtxFactory,
    *,
    reply: str,
    articles: list[dict[str, Any]],
    db_facts: dict[str, Any],
    trigger_key: str,
    router: Router | None = None,
) -> GroundingResult:
    from .structured import complete

    try:
        _, value = complete(
            factory,
            use_case="grounding",
            prompt=load_prompt("grounding"),
            facts=[
                ContextBlock(label="reply", text=reply, untrusted=True),
                ContextBlock(label="cited_articles", text=json.dumps(articles, default=str), untrusted=True),
                ContextBlock(
                    label="db_tools", text=json.dumps(database_values(db_facts), default=str), untrusted=False
                ),
            ],
            output_model=GroundingResult,
            trigger_type="job",
            trigger_key=f"grounding:{trigger_key}",
            router=router,
        )
    except AiError:
        return GroundingResult(verdict="unavailable", claims=[])
    return normalize(value, reply, articles, db_facts)
