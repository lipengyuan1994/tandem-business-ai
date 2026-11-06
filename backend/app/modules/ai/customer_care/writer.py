"""Bounded writer and deterministic multilingual/minimal templates. No notes enter model context."""

from __future__ import annotations

import json
import re
import uuid

from .. import recording
from ..agent.contexts import CtxFactory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..errors import AiError
from ..gateway.router import Router
from ..structured import complete
from ..usecases.briefing import validate_numbers
from .schemas import Draft, Kind, MessageFacts


class TemplateUnavailable(ValueError):
    """Unsupported fallback language requires human writing, never a different customer language."""


def template(kind: Kind, facts: MessageFacts, *, minimal: bool = False) -> Draft:
    lang = facts.language.replace("_", "-").split("-")[0].lower()
    copies = {
        "en": (
            "A message from",
            "How did it go? Reply to contact us.",
            "It may be time to arrange your next appointment. Contact us to book.",
            "Thank you for contacting us. A person can help with your concern.",
            "If you wish, share a review:",
        ),
        "es": (
            "Un mensaje de",
            "¿Cómo le fue? Responda para contactarnos.",
            "Puede ser hora de su próxima cita. Contáctenos para reservar.",
            "Gracias por contactarnos. Una persona puede ayudarle con su consulta.",
            "Si lo desea, comparta una reseña:",
        ),
        "fr": (
            "Un message de",
            "Comment cela s'est-il passé ? Répondez pour nous contacter.",
            "Il est peut-être temps de prévoir votre prochain rendez-vous. Contactez-nous.",
            "Merci de nous avoir contactés. Une personne peut vous aider.",
            "Si vous le souhaitez, laissez un avis :",
        ),
        "zh": (
            "来自",
            "体验如何？您可以回复联系我们。",
            "可能到了安排下次预约的时间。请联系我们预约。",
            "感谢您的联系。工作人员可以协助处理您的问题。",
            "如您愿意，可以留下评价：",
        ),
    }
    if lang not in copies:
        raise TemplateUnavailable("customer_care.language_unavailable")
    title, checkin, recall, complaint, review = copies[lang]
    body = (
        complaint
        if kind == "complaint_reply"
        else recall
        if kind == "recall"
        else review
        if kind == "review_request"
        else checkin
    )
    if not minimal:
        prefix = f"{facts.first_name}, " if facts.first_name and facts.tone == "warm" else ""
        detail = f"{facts.service_name} ({facts.service_date}). " if kind != "complaint_reply" else ""
        body = prefix + detail + body
    body = f"{facts.business_name}: {body}"
    if facts.review_url and kind in {"checkin", "review_request"}:
        body += f"\n{facts.review_url}" if kind == "review_request" else f"\n{review} {facts.review_url}"
    if facts.booking_url and kind == "recall":
        body += f"\n{facts.booking_url}"
    if len(body) > 1000:
        # Drop the greeting and shorten fixed wording; never truncate a supplied fact or URL.
        short = {
            "en": ("How did it go?", "Optional review:", "Contact us for your next appointment."),
            "es": ("¿Cómo le fue?", "Reseña opcional:", "Contáctenos para su próxima cita."),
            "fr": (
                "Comment cela s'est-il passé ?",
                "Avis facultatif :",
                "Contactez-nous pour votre prochain rendez-vous.",
            ),
            "zh": ("体验如何？", "如您愿意，请留下评价：", "请联系我们安排下次预约。"),
        }[lang]
        question = short[2] if kind == "recall" else short[1] if kind == "review_request" else short[0]
        body = f"{facts.business_name}: {facts.service_name} ({facts.service_date}). {question}"
        if facts.review_url and kind in {"checkin", "review_request"}:
            body += f"\n{short[1]} {facts.review_url}"
        if facts.booking_url and kind == "recall":
            body += f"\n{facts.booking_url}"
    return Draft(subject=f"{title} {facts.business_name}"[:160], body=body)


def validate(draft: Draft, facts: MessageFacts, kind: Kind) -> None:
    text = draft.subject + " " + draft.body
    for required in [
        facts.business_name,
        *([facts.service_name, facts.service_date] if kind in {"checkin", "recall"} else []),
    ]:
        if required and required not in text:
            raise ValueError("required fact missing")
    rest = text
    for value in sorted([str(v) for v in facts.model_dump().values() if v], key=len, reverse=True):
        rest = rest.replace(value, "")
    validate_numbers(rest, "")
    if (
        not facts.review_url
        and kind != "review_request"
        and (
            re.search(r"\b(review|reseña|avis)\b", rest, re.I)
            or any(term in rest for term in ("评价", "評價", "レビュー", "리뷰", "รีวิว"))
        )
    ):
        raise ValueError("review request requires consent and a supplied link")
    if facts.tone == "concise" and len(draft.body) > 500:
        raise ValueError("concise message is too long")
    if facts.tone == "formal" and re.search(r"\b(hey|awesome|cool)\b", rest, re.I):
        raise ValueError("formal tone required")
    if re.search(r"\d|[$€£¥%]|https?://|www\.", rest) or re.search(
        r"\b(discount|offer|free|price|refund|guarantee|diagnos\w*|medication|prescri\w*|insurance|diagnosis|clinical|disease|cancer|antibiotic|dosage|cure|dollars?|euros?|descuento|gratis|rabais)\b",
        rest,
        re.I,
    ):
        raise ValueError("unsupported offer, price, link or clinical detail")


def write(
    factory: CtxFactory,
    kind: Kind,
    facts: MessageFacts,
    trigger_key: str,
    *,
    minimal: bool,
    router: Router | None = None,
) -> tuple[uuid.UUID, Draft]:
    prompt = load_prompt("customer_care_message", 1)
    if not minimal:
        try:
            return complete(
                factory,
                use_case="customer_care",
                prompt=prompt,
                facts=[
                    ContextBlock(
                        label="message_facts", text=json.dumps({"kind": kind, **facts.model_dump()}), untrusted=True
                    )
                ],
                output_model=Draft,
                trigger_type="job",
                trigger_key=trigger_key,
                router=router,
                validate=lambda draft: validate(draft, facts, kind),
            )
        except AiError as exc:
            if exc.code.startswith("ai.chain_") or exc.code in {"ai.agent_disabled", "ai.agent_limit_reached"}:
                raise
            # The failed generation remains visible with its actual usage/status. The template has no model call.
            fallback_reason = exc.code
    else:
        fallback_reason = "customer_care.minimal_template"
    draft = template(kind, facts, minimal=minimal)
    ctx = factory(uuid.uuid4())
    result = recording.record_early_result(
        ctx,
        recording.RunMeta(use_case="customer_care", prompt=prompt, trigger_type="job", trigger_key=trigger_key),
        needs_human=kind == "complaint_reply",
        result={"template": True, "reason": fallback_reason},
    )
    return result.run_id, draft
