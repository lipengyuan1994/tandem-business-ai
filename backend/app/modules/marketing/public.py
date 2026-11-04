"""Public API of the marketing module. Other modules may import ONLY from this file."""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import service
from .models import CampaignRevision
from .schemas import AudiencePreview, AudienceSample, CampaignOut, CampaignRevisionOut
from .service import CampaignSent


class SegmentBrief(BaseModel):
    name: str
    filter_description: str


def get_segment(db: Session, ctx: Context, segment_id: uuid.UUID) -> SegmentBrief:
    """Configured filters only; never evaluate the segment or return audience/customer data."""
    if not ctx.has("marketing.campaign.write"):
        ctx.require("marketing.campaign.read")
    segment = service.get_segment(db, ctx, segment_id)
    definition = service.parse_definition(segment.definition)
    description = "; ".join(f"{c.field} {c.op} {json.dumps(c.value, ensure_ascii=False)}" for c in definition.all)
    return SegmentBrief(name=segment.name, filter_description=f"All conditions must match: {description}")


def preview_audience(db: Session, ctx: Context, segment_id: uuid.UUID) -> AudiencePreview:
    audience = service.preview_audience(db, ctx, segment_id)
    members = [AudienceSample(customer_id=m.customer_id, display_name=m.display_name) for m in audience.members]
    return AudiencePreview(
        matched=audience.matched,
        eligible_count=len(members),
        excluded_counts=audience.excluded,
        sample=members[:10],
        offset=0,
        members=members[:10],
        hash=audience.hash,
    )


def get_campaign(db: Session, ctx: Context, campaign_id: uuid.UUID) -> CampaignOut:
    from .api import campaign_out

    return campaign_out(db, ctx, service.get_campaign(db, ctx, campaign_id))


def add_revision(
    db: Session,
    ctx: Context,
    campaign_id: uuid.UUID,
    subject: str,
    body_markdown: str,
    *,
    created_by_kind: str,
    ai_run_id: uuid.UUID | None = None,
) -> CampaignRevisionOut:
    row: CampaignRevision = service.add_revision(
        db, ctx, campaign_id, subject, body_markdown, created_by_kind=created_by_kind, ai_run_id=ai_run_id
    )
    return CampaignRevisionOut.model_validate(row)


__all__ = ["CampaignSent", "SegmentBrief", "add_revision", "get_campaign", "get_segment", "preview_audience"]
