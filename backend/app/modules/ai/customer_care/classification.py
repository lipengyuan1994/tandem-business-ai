"""Independent inbound Care admission: keywords hold immediately; bounded safety-class semantics otherwise.

Never calls Front Desk or sends a reply. One durable job per message, no job retry, the normal structured
call/repair limit and shared public hourly run ceiling, plus B1 provider/agent/chain budgets. Paused threads
and open reply proposals do not gate this work. Only the inbound body enters the classifier, fenced as data.
"""

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.conversations import public as conversations
from app.platform import jobs
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import NotFound

from ..agent.contexts import background_ctx_factory
from ..errors import AiError
from ..models import AiRun
from ..public_intake import admit_classification
from ..schemas import SafetyVerdict
from ..subscribers import _domain_context
from ..team_settings import enabled
from ..usecases import safety
from . import triage
from .models import CareCase


def keyword_category(body: str) -> str | None:
    # Additional conservative positive signal, never a negative classification.
    for category, pattern in (
        ("dispute", r"\b(dispute|chargeback)\b|争议|爭議"),
        ("warranty", r"\b(warranty claim|warranty help|garantía|réclamation)\b|保修|保固"),
        ("complaint", r"\b(complaint|complain|queja)\b|投诉|投訴"),
    ):
        if re.search(pattern, body, re.I):
            return category
    return None


def trigger(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    if payload.get("author_kind") not in {"visitor", "customer"}:
        return
    ctx = _domain_context(ctx, event_id)
    if not enabled(db, ctx, "customer_care", lock=True):
        return
    assert ctx.business_id is not None
    read = background_ctx_factory(ctx.business_id, use_case="customer_care", parent=ctx)(uuid.uuid4())
    message_id = uuid.UUID(str(payload["message_id"]))
    try:
        source = conversations.get_message_context(db, read, message_id)
    except NotFound:
        return
    if source.author_kind not in {"visitor", "customer"}:
        return
    if category := keyword_category(source.body):
        triage.enqueue(db, read, message_id, category)
        return
    jobs.enqueue(
        db,
        "ai.customer_care_classify",
        {"message_id": str(message_id)},
        business_id=ctx.business_id,
        dedupe_key=f"care-classify:{ctx.business_id}:{message_id}",
        priority=10,
        max_attempts=1,
        ctx=read,
    )


def run(ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    factory = background_ctx_factory(ctx.business_id, use_case="customer_care", parent=ctx)
    read = factory(uuid.uuid4())
    message_id = uuid.UUID(str(payload["message_id"]))
    key = f"care-classify:{message_id}"
    with transaction(read) as db:
        if not enabled(db, read, "customer_care", lock=True):
            return
        source = conversations.get_message_context(db, read, message_id)
        if source.author_kind not in {"visitor", "customer"} or db.scalar(
            select(CareCase.id).where(
                CareCase.business_id == ctx.business_id,
                CareCase.source_message_id == message_id,
            )
        ):
            return
        prior = db.scalar(
            select(AiRun)
            .where(
                AiRun.business_id == ctx.business_id,
                AiRun.trigger_key == f"safety:{key}",
            )
            .order_by(AiRun.created_at.desc())
            .limit(1)
        )
        verdict = SafetyVerdict.model_validate(prior.result) if prior and prior.status == "succeeded" else None
        attempted = prior is not None
        if not attempted and not admit_classification(db, read, message_id):
            return
    if not attempted:
        try:
            verdict = safety.screen(factory, source.body, key)
        except AiError as exc:
            if exc.code.startswith("ai.chain_") or exc.code in {"ai.agent_disabled", "ai.agent_limit_reached"}:
                raise
            verdict = None
    with transaction(read) as db:
        if not enabled(db, read, "customer_care", lock=True):
            return
        if verdict is None:
            conversations.request_handoff(
                db, read, source.conversation_id, "Customer Care classification unavailable; please assess"
            )
            return
        if verdict.care_category != "none":
            # Persist the dispute hold before any suggested-reply generation; human mode is allowed.
            triage.enqueue(db, read, message_id, verdict.care_category, verdict.care_priority)
        if verdict.flagged or verdict.care_category != "none":
            conversations.request_handoff(db, read, source.conversation_id, "Customer Care concern requires a person")
