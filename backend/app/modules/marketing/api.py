"""/api/v1/marketing and the public unsubscribe link."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.platform import ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import public_context, require

from . import permissions as P
from . import service
from .models import Campaign
from .schemas import (
    AudiencePreview,
    AudienceSample,
    CampaignCreate,
    CampaignDetail,
    CampaignOut,
    CampaignRevisionIn,
    CampaignRevisionOut,
    CampaignUpdate,
    SegmentIn,
    SegmentOut,
    SnapshotOut,
    SuppressionIn,
    SuppressionOut,
)

router = APIRouter()
public_router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def campaign_out(db: Session, ctx: Context, c: Campaign) -> CampaignOut:
    revision = service._revision(db, ctx, c.current_revision_id)
    snapshot = service.snapshot_of(db, ctx, c.submitted_snapshot_id or c.approved_snapshot_id)
    return CampaignOut(
        id=c.id,
        name=c.name,
        purpose=c.purpose,
        channel=c.channel,
        segment_id=c.segment_id,
        status=c.status,
        current_revision=CampaignRevisionOut.model_validate(revision) if revision else None,
        snapshot=SnapshotOut.model_validate(snapshot) if snapshot else None,
        proposal_id=c.proposal_id,
        sent_at=c.sent_at,
        version=c.version,
        created_at=c.created_at,
    )


@router.get("/segments", response_model=list[SegmentOut], summary="Saved audiences", responses=ERRORS)
def list_segments(ctx: Context = Depends(require(P.CAMPAIGN_READ))) -> list[SegmentOut]:
    with transaction(ctx) as db:
        return [SegmentOut.model_validate(s) for s in service.list_segments(db, ctx)]


@router.post("/segments", response_model=SegmentOut, status_code=201, summary="Save an audience", responses=ERRORS)
def create_segment(body: SegmentIn, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))) -> SegmentOut:
    with transaction(ctx) as db:
        return SegmentOut.model_validate(service.create_segment(db, ctx, body))


@router.get(
    "/segments/{segment_id}/preview",
    response_model=AudiencePreview,
    summary="Who would receive a campaign",
    responses=ERRORS,
)
def preview(
    segment_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=200),
    ctx: Context = Depends(require(P.CAMPAIGN_READ)),
) -> AudiencePreview:
    """Eligible count, the reasons others are excluded, and a page of the full eligible list."""
    with transaction(ctx) as db:
        audience = service.preview_audience(db, ctx, segment_id)
    members = [AudienceSample(customer_id=m.customer_id, display_name=m.display_name) for m in audience.members]
    return AudiencePreview(
        matched=audience.matched,
        eligible_count=len(members),
        excluded_counts=audience.excluded,
        sample=members[:10],
        offset=offset,
        members=members[offset : offset + limit],
        hash=audience.hash,
    )


@router.get("/campaigns", response_model=list[CampaignOut], summary="Campaigns, newest first", responses=ERRORS)
def list_campaigns(ctx: Context = Depends(require(P.CAMPAIGN_READ))) -> list[CampaignOut]:
    with transaction(ctx) as db:
        return [campaign_out(db, ctx, c) for c in service.list_campaigns(db, ctx)]


@router.post(
    "/campaigns", response_model=CampaignOut, status_code=201, summary="Start a campaign draft", responses=ERRORS
)
def create_campaign(body: CampaignCreate, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))) -> CampaignOut:
    with transaction(ctx) as db:
        return campaign_out(db, ctx, service.create_campaign(db, ctx, body))


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignDetail,
    summary="One campaign with its revisions",
    responses=ERRORS,
)
def get_campaign(campaign_id: uuid.UUID, ctx: Context = Depends(require(P.CAMPAIGN_READ))) -> CampaignDetail:
    with transaction(ctx) as db:
        c = service.get_campaign(db, ctx, campaign_id)
        out = campaign_out(db, ctx, c)
        revisions = [CampaignRevisionOut.model_validate(r) for r in service.revisions_of(db, ctx, campaign_id)]
        return CampaignDetail(**out.model_dump(), revisions=revisions)


@router.patch(
    "/campaigns/{campaign_id}", response_model=CampaignOut, summary="Rename, or change the audience", responses=ERRORS
)
def update_campaign(
    campaign_id: uuid.UUID, body: CampaignUpdate, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))
) -> CampaignOut:
    with transaction(ctx) as db:
        return campaign_out(db, ctx, service.update_campaign(db, ctx, campaign_id, body))


@router.post(
    "/campaigns/{campaign_id}/revisions",
    response_model=CampaignRevisionOut,
    status_code=201,
    summary="Edit the message (adds a revision)",
    responses=ERRORS,
)
def add_revision(
    campaign_id: uuid.UUID, body: CampaignRevisionIn, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))
) -> CampaignRevisionOut:
    with transaction(ctx) as db:
        return CampaignRevisionOut.model_validate(
            service.add_revision(db, ctx, campaign_id, body.subject, body.body_markdown)
        )


@router.post(
    "/campaigns/{campaign_id}/submit",
    response_model=CampaignOut,
    summary="Freeze the audience and ask for approval",
    responses=ERRORS,
)
def submit(campaign_id: uuid.UUID, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))) -> CampaignOut:
    with transaction(ctx) as db:
        return campaign_out(db, ctx, service.submit_for_review(db, ctx, campaign_id))


@router.post(
    "/campaigns/{campaign_id}/cancel",
    response_model=CampaignOut,
    summary="Cancel a draft or pending campaign",
    responses=ERRORS,
)
def cancel(campaign_id: uuid.UUID, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))) -> CampaignOut:
    with transaction(ctx) as db:
        return campaign_out(db, ctx, service.cancel_campaign(db, ctx, campaign_id))


@router.get(
    "/suppressions",
    response_model=list[SuppressionOut],
    summary="Addresses that never receive marketing",
    responses=ERRORS,
)
def list_suppressions(ctx: Context = Depends(require(P.CAMPAIGN_READ))) -> list[SuppressionOut]:
    with transaction(ctx) as db:
        return [SuppressionOut.model_validate(s) for s in service.list_suppressions(db, ctx)]


@router.post(
    "/suppressions", response_model=SuppressionOut, status_code=201, summary="Suppress an address", responses=ERRORS
)
def add_suppression(body: SuppressionIn, ctx: Context = Depends(require(P.CAMPAIGN_WRITE))) -> SuppressionOut:
    with transaction(ctx) as db:
        return SuppressionOut.model_validate(service.add_suppression(db, ctx, body.value, body.reason))


PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>'
    '<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem">'
    "<h1>{title}</h1><p>{text}</p></body></html>"
)


@public_router.get(
    "/{business_slug}/unsubscribe", response_class=HTMLResponse, summary="Unsubscribe link in marketing e-mails"
)
def unsubscribe(token: str, request: Request, ctx: Context = Depends(public_context)) -> HTMLResponse:
    """Withdraws marketing consent and suppresses the address. Idempotent; shows the same page when repeated."""
    with transaction(ctx) as db:
        ratelimit.hit(
            db, f"unsub:{request.client.host if request.client else 'unknown'}", limit=30, window_seconds=3600
        )
        service.unsubscribe(db, ctx, token)
    return HTMLResponse(
        PAGE.format(title="You are unsubscribed", text="You will not receive marketing e-mails from us any more.")
    )


@public_router.post(
    "/{business_slug}/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click unsubscribe (RFC 8058)",
    responses=error_responses(404, 422, 429),
)
def one_click_unsubscribe(
    token: str,
    request: Request,
    list_unsubscribe: Literal["One-Click"] = Form(alias="List-Unsubscribe"),
    ctx: Context = Depends(public_context),
) -> HTMLResponse:
    """Public signed-token action: no session, CSRF token or redirect; same scope and rate limit as GET."""
    return unsubscribe(token, request, ctx)
