"""U6: permission-filtered daily facts, one structured writer, per-user UTC-hour cache."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select

from app.modules.dashboard import public as dashboard
from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction

from ..agent.contexts import staff_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router
from ..models import AiRun
from ..schemas import Briefing
from ..structured import complete
from ..tools.common import local_label, redact_contacts

# Compare complete numeric tokens, never substrings (5 does not occur in 50).
_NUMBER = re.compile(r"[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?")
_NUMBER_WORD = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    # Ordinals ("first", "second") are left out: they are ordinary words ("first thing", "wait a second").
    r"hundred|thousand|million|billion|trillion)\b",
    re.IGNORECASE,
)


def validate_numbers(output: str, facts: str) -> None:
    """Reject numeric tokens or English number words absent from the exact facts supplied to the model."""
    if not set(_NUMBER.findall(output)) <= set(_NUMBER.findall(facts)) or not {
        w.lower() for w in _NUMBER_WORD.findall(output)
    } <= {w.lower() for w in _NUMBER_WORD.findall(facts)}:
        raise ValueError("Every number must appear verbatim in the supplied facts; remove unsupported numbers.")


class BriefingOut(BaseModel):
    generated_at: datetime
    bullets: list[str]


def run(ctx: Context, *, router: Router | None = None) -> BriefingOut:
    ctx.require("ai.assistant.use")
    assert ctx.business_id is not None and ctx.user_id is not None
    factory = staff_ctx_factory(ctx.business_id, ctx.user_id, ctx.permissions, "briefing")
    hour = clock.now().strftime("%Y-%m-%dT%H")
    trigger_key = f"briefing:{ctx.user_id}:{hour}"
    with transaction(ctx) as db:
        cached = db.scalar(select(AiRun).where(
            AiRun.business_id == ctx.business_id, AiRun.use_case == "briefing", AiRun.trigger_key == trigger_key,
            AiRun.on_behalf_of_user_id == ctx.user_id, AiRun.status == "succeeded",
        ).order_by(AiRun.started_at.desc(), AiRun.id.desc()).limit(1))  # fmt: skip
        if cached is not None:
            return BriefingOut(generated_at=cached.finished_at or cached.started_at,
                               bullets=Briefing.model_validate(cached.result).bullets)  # fmt: skip
    read_ctx = factory(uuid.uuid4())
    with transaction(read_ctx) as db:
        s = dashboard.today_summary(db, read_ctx)
    upcoming = (
        []
        if s.agenda is None
        else [a for a in s.agenda if a.ends_at >= clock.now() and a.status in {"confirmed", "held"}]
    )
    data = {
        "local_date": s.local_date.isoformat(),
        "timezone": s.timezone,
        "agenda": None
        if s.agenda is None
        else {
            "count": len(s.agenda),
            "next": [
                {
                    "customer_name": a.customer_name,
                    "service_name": a.service_name,
                    "resource_name": a.resource_name,
                    "local_label": local_label(a.starts_at, a.timezone),
                    "status": a.status,
                }
                for a in upcoming[:5]
            ],
        },
        "waiting_conversations": s.conversations,
        "approvals": None
        if s.approvals is None
        else {
            "can_decide": s.approvals.can_decide,
            "top": [{"title": p.title, "action_type": p.action_type} for p in s.approvals.top[:5]],
        },
        "low_stock": None
        if s.low_stock is None
        else [x.model_dump(mode="json", exclude={"item_id"}) for x in s.low_stock],
        "delivery_issues": s.delivery_issues,
    }
    facts = redact_contacts(json.dumps(data, ensure_ascii=False)) or "{}"
    run_id, value = complete(
        factory, use_case="briefing", prompt=load_prompt("briefing"),
        facts=[ContextBlock(label="today_summary", text=facts, untrusted=True)],
        output_model=Briefing, trigger_type="api", trigger_key=trigger_key, router=router,
        validate=lambda value: validate_numbers("\n".join(value.bullets), facts),
    )  # fmt: skip
    with transaction(ctx) as db:
        recorded = db.get(AiRun, run_id)
        assert recorded is not None and recorded.finished_at is not None
        return BriefingOut(generated_at=recorded.finished_at, bullets=value.bullets)
