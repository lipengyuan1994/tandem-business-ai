"""Conservative, deterministic preservation checks; no model or external language packages."""

import re
from decimal import Decimal

SMALL = dict(
    zip(
        (
            "zero one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
        ).split(),
        range(20),
        strict=True,
    )
)
SMALL.update(
    dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10), strict=True))
)
SMALL.update(
    dict(
        zip(
            (
                "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
                "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth"
            ).split(),
            range(1, 21),
            strict=True,
        )
    )
)
SMALL.update({"a": 1, "an": 1})  # used only before hundred/thousand
MONTHS = dict(
    zip(
        "january february march april may june july august september october november december".split(),
        range(1, 13),
        strict=True,
    )
)
CURRENCIES = {
    "USD": {"$", "usd", "dollar", "dollars"},
    "EUR": {"€", "eur", "euro", "euros"},
    "GBP": {"£", "gbp", "pound", "pounds"},
    "JPY": {"¥", "jpy", "yen"},
}
WORDS = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
DIGITS = re.compile(r"(?<!\w)[+-]\d+(?:[,.]\d+)*(?:st|nd|rd|th)?|\d+(?:[,.]\d+)*(?:st|nd|rd|th)?", re.I)


def clean(raw: str) -> str:
    return " ".join(raw.split())


def numbers(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for match in DIGITS.finditer(text):
        value = re.sub(r"(st|nd|rd|th)$", "", match.group(), flags=re.I)
        # US thousands; decimal commas are preserved as an alternate interpretation.
        if "," in value and "." not in value and not re.fullmatch(r"\d{1,3}(,\d{3})+", value):
            out.add(Decimal(value.replace(",", ".")))
        out.add(Decimal(value.replace(",", "")))
    words = re.findall(r"\d+|[^\W\d_]+", text.casefold())
    for i, word in enumerate(words):
        if word in MONTHS:
            out.add(Decimal(MONTHS[word]))
        if word in SMALL and word not in {"a", "an"}:
            out.add(Decimal(SMALL[word]))
        if word not in SMALL and word not in {"hundred", "thousand", "million"}:
            continue
        total, current = 0, 0
        previous = ""
        last_scale = 1_000_000_000
        for part in words[i:]:
            if part in SMALL:
                word_value = SMALL[part]
                if previous in {"ones", "tens"} and not (previous == "tens" and 0 < word_value < 10):
                    break
                current += word_value
                previous = "tens" if word_value >= 20 else "ones"
            elif part == "hundred":
                if current >= 100 or previous in {"hundred", "large"}:
                    break
                current = (current or 1) * 100
                previous = "hundred"
            elif part in {"thousand", "million"}:
                scale = 1000 if part == "thousand" else 1_000_000
                if scale >= last_scale or previous == "large":
                    break
                total += (current or 1) * scale
                current, last_scale, previous = 0, scale, "large"
            elif part == "and":
                if total + current < 100 or previous == "and":
                    break
                previous = "and"
                continue
            else:
                break
            out.add(Decimal(total + current))
    # Explicit spoken decimal, including zero-leading fractional digits.
    normalized = text.casefold()
    for word, digit_value in sorted(SMALL.items(), key=lambda pair: -len(pair[0])):
        if digit_value < 10 and word not in {"a", "an"}:
            normalized = re.sub(r"\b" + word + r"\b", str(digit_value), normalized)
    for match in re.finditer(r"\b(\d+)\s+point\s+((?:\d\s*)+)", normalized):
        out.add(Decimal(match[1] + "." + re.sub(r"\s", "", match[2])))
    number_words = "|".join([*SMALL, "hundred", "thousand", "million", "and"])
    pattern = r"\b((?:(?:" + number_words + r")\s+)+)point\s+((?:\d\s*)+)"
    for match in re.finditer(pattern, normalized):
        left = numbers(match[1])
        if left:
            out.add(Decimal(str(max(left)) + "." + re.sub(r"\s", "", match[2])))
    return out


def currencies(text: str) -> set[str]:
    words = set(WORDS.findall(text.casefold()))
    return {
        code for code, forms in CURRENCIES.items() if words & forms or any(c in text for c in forms if not c.isalpha())
    }


def guard(raw: str, polished: str, dictionary: list[str]) -> str | None:
    raw = clean(raw)
    if raw and not polished.strip():
        return "voice.polish_empty"
    if not numbers(polished) <= numbers(raw):
        return "voice.polish_new_number"
    if not currencies(polished) <= currencies(raw):
        return "voice.polish_new_currency"
    # Checking all novel words is stricter than guessing which are proper nouns. It also catches lower-case
    # invented names and prevents translation. False positives deliberately retain the original speech.
    permitted = set(WORDS.findall((raw + " " + " ".join(dictionary)).casefold()))
    for code, forms in CURRENCIES.items():
        if code in currencies(raw):
            permitted |= {f for f in forms if f.isalpha()}
    novel = set(WORDS.findall(polished.casefold())) - permitted
    # Numeric ordinal suffixes are formatting, not names.
    novel -= {"st", "nd", "rd", "th"}
    if novel:
        return "voice.polish_new_name"
    if len(polished) > len(raw) * 1.2:
        return "voice.polish_too_long"
    return None
