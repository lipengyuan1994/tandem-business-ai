"""Conservative locale keyword backstop. No negation inference and no medical advice."""

import re
import unicodedata

KEYWORDS = {
    "en": (
        "emergency",
        "urgent",
        "pain",
        "swelling",
        "swollen",
        "bleeding",
        "bleed",
        "trauma",
        "injury",
        "injured",
        "knocked out",
        "knocked-out",
        "can't breathe",
        "cannot breathe",
        "trouble breathing",
        "difficulty breathing",
        "unconscious",
        "fainted",
        "fever",
        "infection",
        "abscess",
    ),
    "es": (
        "emergencia",
        "urgente",
        "dolor",
        "hinchazon",
        "hinchado",
        "sangrado",
        "sangrando",
        "lesion",
        "respirar",
        "desmayado",
        "fiebre",
        "infeccion",
    ),
    "zh": ("急诊", "急救", "紧急", "疼", "痛", "肿", "流血", "出血", "呼吸困难", "昏迷", "发烧"),
}


def signals_emergency(body: str, locale: str) -> bool:
    text = "".join(
        c for c in unicodedata.normalize("NFKD", body.casefold().replace("’", "'")) if not unicodedata.combining(c)
    )
    # English is the fallback in every locale; also recognize supported multilingual input.
    language = locale.split("-")[0].lower()
    words = KEYWORDS["en"] + KEYWORDS.get(language, ()) + KEYWORDS["es"]
    return any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) for word in words) or any(
        word in text for word in KEYWORDS["zh"]
    )
