"""U5: write a campaign revision from metadata only; audience and delivery stay in marketing."""

from __future__ import annotations

import html
import json
import re
import uuid
from dataclasses import replace
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from app.modules.marketing import public as marketing
from app.modules.org import public as org
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict

from ..agent.contexts import staff_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router
from ..schemas import CampaignDraft
from ..structured import complete
from ..tools.common import redact_contacts

_URL = re.compile(
    r"\b[a-z][a-z0-9+.-]*:(?://|[^\s])|www\.|//|"
    r"\b(?:[\w-]+\.)+[a-z\u0080-\uffff]{2,63}\b|"
    r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)|"
    r"\blocalhost\b|!?\[[^\]]*\]\(\s*[^)\s]+|(?:^|[\s(<])(?:/|\.\.?/)[^\s)>]+",
    re.I,
)


class CampaignDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str | None = Field(default=None, max_length=1000)


class CampaignDraftOut(BaseModel):
    run_id: uuid.UUID
    revision_id: uuid.UUID


def _validate(value: CampaignDraft) -> None:
    for text in (value.subject, value.body_markdown):
        if not text.strip():
            raise ValueError("Subject and body must not be blank.")
        # Check common encoded forms as well as literal links in either field.
        decoded = unquote(html.unescape(text))
        if "unsubscribe" in decoded.casefold() or _URL.search(decoded):
            raise ValueError("Remove every URL and unsubscribe text; the delivery template adds its own footer.")


def run(
    ctx: Context, campaign_id: uuid.UUID, *, purpose: str | None = None, router: Router | None = None
) -> CampaignDraftOut:
    ctx.require("ai.assistant.use")
    ctx.require("marketing.campaign.write")
    assert ctx.business_id is not None and ctx.user_id is not None
    factory = staff_ctx_factory(ctx.business_id, ctx.user_id, ctx.permissions, "campaign_draft", parent=ctx)
    read_ctx = factory(uuid.uuid4())
    with transaction(read_ctx) as db:
        campaign = marketing.get_campaign(db, read_ctx, campaign_id)
        if campaign.status not in {"draft", "in_review"}:
            raise Conflict("marketing.campaign_locked", current_status=campaign.status)
        segment = marketing.get_segment(db, read_ctx, campaign.segment_id)
        business = org.get_business_brief(db, read_ctx)
    data = {
        "business": {"name": business.name, "timezone": business.timezone,
                     "currency": business.currency, "locale": business.locale},
        "campaign": {"name": campaign.name, "purpose": purpose if purpose is not None else campaign.purpose},
        "segment": segment.model_dump(),
    }  # fmt: skip
    facts = redact_contacts(json.dumps(data, ensure_ascii=False)) or "{}"
    trigger_key = f"campaign_draft:{campaign_id}:{uuid.uuid4()}"
    run_id, value = complete(
        factory, use_case="campaign_draft", prompt=load_prompt("campaign_draft"),
        facts=[ContextBlock(label="campaign", source_id=campaign_id, text=facts, untrusted=True)],
        output_model=CampaignDraft, trigger_type="api", trigger_key=trigger_key, router=router, validate=_validate,
    )  # fmt: skip
    assistant = replace(factory(run_id), causation_id=run_id)
    from .. import recording
    from ..chains import require_enabled
    from ..errors import AiError
    from ..team_budget import reserve_action, stop_chain

    try:
        with transaction(assistant) as db:
            require_enabled(db, assistant)
            reserve_action(db, assistant)
            revision = marketing.add_revision(
                db,
                assistant,
                campaign_id,
                value.subject,
                value.body_markdown,
                created_by_kind="assistant",
                ai_run_id=run_id,
            )
    except AiError as exc:
        if exc.code.startswith("ai.chain_"):
            with transaction(assistant) as db:
                stop_chain(db, assistant, exc.code)
        recording.close_run(assistant, run_id, "policy_blocked", error_code=exc.code, needs_human=True)
        raise
    return CampaignDraftOut(run_id=run_id, revision_id=revision.id)
