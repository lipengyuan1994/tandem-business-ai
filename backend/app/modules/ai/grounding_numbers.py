"""Field-bound operational number checks; a duration must never authorize a price."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from .usecases.briefing import validate_numbers

PRICE = re.compile(r"\b(?:prices?|costs?|charges?|USD|GBP|EUR|dollars?|pounds?|euros?)\b|[$£€]", re.I)
DURATION = re.compile(r"\b(?:duration|minutes?|takes?|buffer)\b", re.I)
STOCK = re.compile(r"\b(?:stock|available|on.hand|remaining|left|reserved)\b", re.I)
HOURS = re.compile(r"\b(?:opens?|closes?|hours?)\b|\d{1,2}:\d{2}|\d\s*(?:am|pm)\b", re.I)
CURRENCY = re.compile(r"\b(?:USD|GBP|EUR|dollars?|pounds?|euros?)\b|[$£€]", re.I)
ALIASES = {
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "£": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "€": "EUR",
    "euro": "EUR",
    "euros": "EUR",
}


class MissingFact(ValueError):
    """Missing or ambiguous typed source; retain the reply for staff."""


def policy_number_text(text: str) -> str:
    """Canonicalize small English integer spellings for policy periods, without changing DB field checks."""
    names = (
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen"
    ).split()
    values = {name: i for i, name in enumerate(names)}
    values.update(
        {name: i * 10 for i, name in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
    )
    tens = "|".join(name for name, value in values.items() if value >= 20)
    units = "|".join(names[1:10])
    text = re.sub(
        rf"\b({tens})[ -]({units})\b",
        lambda m: str(values[m[1].lower()] + values[m[2].lower()]),
        text,
        flags=re.I,
    )
    return re.sub(r"\b(" + "|".join(values) + r")\b", lambda m: str(values[m[0].lower()]), text, flags=re.I)


def _amount(minor: int, currency: str) -> str:
    amount = Decimal(minor) / 100
    return f"{amount:.2f} {amount.normalize():f} {currency}"


def field_facts(value: Any) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []  # kind, subject, exact field values

    def walk(value: Any, subject: str = "", currency: str = "") -> None:
        if isinstance(value, list):
            for v in value:
                walk(v, subject, currency)
        elif isinstance(value, dict):
            subject = str(value.get("name") or value.get("item_name") or value.get("service_name") or subject)
            currency = str(value.get("currency") or currency)
            minor = value.get("minor", value.get("price_minor"))
            if isinstance(minor, int) and currency:
                out.append(("price", subject, _amount(minor, currency)))
            if isinstance(value.get("price"), str):
                out.append(("price", subject, value["price"]))
            for key in ("duration_minutes", "buffer_minutes"):
                if value.get(key) is not None:
                    out.append(("buffer" if key == "buffer_minutes" else "duration", subject, str(value[key])))
            for key in ("available", "on_hand", "reserved", "quantity_available"):
                if value.get(key) is not None:
                    out.append(("available" if key == "quantity_available" else key, subject, str(value[key])))
            for key in ("opens", "closes", "start_time", "end_time", "starts_at", "ends_at", "local_label"):
                if value.get(key) is not None:
                    out.append(("hours", subject, str(value[key])))
            for k, v in value.items():
                if isinstance(v, dict | list):
                    walk(v, subject, currency)
                elif isinstance(v, str) and k not in {"name", "item_name", "service_name", "price"}:
                    # Text-form synthetic/tool facts may spell out a price or complete operational fact.
                    if CURRENCY.search(v):
                        out.append(("price", subject, v))
        elif isinstance(value, str):
            if CURRENCY.search(value):
                out.append(("price", subject, value))

    walk(value)
    return out


def validate_database_numbers(claim: str, db_facts: dict[str, Any]) -> None:
    typed = field_facts(db_facts)
    subjects = {name for _, name, _ in typed if name and name.casefold() in claim.casefold()}
    if len(subjects) > 1 and re.search(r"\d", claim):
        raise MissingFact("Split numeric assertions by named service or item.")
    for part in re.split(r"\s+(?:and|but|while)\s+|(?<=[.!?])\s+", claim, flags=re.I):
        kinds = []
        if PRICE.search(part):
            kinds.append("price")
        if DURATION.search(part):
            kinds.append("buffer" if re.search(r"\bbuffer\b", part, re.I) else "duration")
        if HOURS.search(part) and not {"duration", "buffer"} & set(kinds):
            kinds.append("hours")
        if STOCK.search(part) and "hours" not in kinds:
            kinds.append(
                "on_hand"
                if re.search(r"\bon.hand\b", part, re.I)
                else "reserved"
                if re.search(r"\breserved\b", part, re.I)
                else "available"
            )
        if not kinds:
            continue
        if len(kinds) > 1 and re.search(r"\d", part):
            raise MissingFact("Split numeric assertions by operational field.")
        candidates = {name for kind, name, _ in typed if kind == kinds[0] and name}
        if not subjects and len(candidates) > 1:
            raise MissingFact("Name the exact database service or item for this numeric assertion.")
        sources = [v for kind, name, v in typed if kind == kinds[0] and (not subjects or name in subjects)]
        if not sources:
            raise MissingFact("No database field supports this operational claim.")
        facts = json.dumps(sources)
        if kinds[0] == "price":
            for unit in CURRENCY.findall(part):
                currency = ALIASES.get(unit.casefold(), unit.upper())
                if currency not in facts:
                    raise ValueError("Currency differs from database price.")
        # Names may contain numeric tokens/words (700c, Brake pads (pair)); they are identities, not quantities.
        assertion = part
        for subject in subjects:
            assertion = re.sub(re.escape(subject), "", assertion, flags=re.I)
        validate_numbers(assertion, facts)
