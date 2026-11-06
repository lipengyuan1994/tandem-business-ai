"""/api/v1/ai (docs/roadmap/ai/contracts.md §14). AI-01: `/status`; AI-05: `/runs`, `/usage`; later cards the rest."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.modules.approvals import public as approvals
from app.platform import crud
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.pagination import Page
from app.platform.permissions import require

from . import permissions as P
from . import public, usage
from .autonomy_api import router as autonomy_router
from .customer_care.api import router as care_router
from .models import AiRun
from .schemas import AiStatus, RunOut, RunStatus, UsageOut, UseCaseId
from .team_api import router as team_router
from .usecases import briefing, campaign_draft, copilot, copilot_cards, procurement, summary
from .work_queue_api import router as work_queue_router

router = APIRouter()
router.include_router(autonomy_router)
router.include_router(work_queue_router)
router.include_router(team_router)

router.include_router(care_router)
ERRORS = error_responses(401, 403)


@router.get("/status", response_model=AiStatus, summary="Whether AI features are available", responses=ERRORS)
def get_status(ctx: Context = Depends(require(P.ASSISTANT_USE))) -> AiStatus:
    with transaction(ctx) as db:
        return public.status(db, ctx)


@router.get(
    "/runs",
    response_model=Page[RunOut],
    summary="Recorded AI runs, newest first (no prompt, completion or result text)",
    responses=error_responses(400, 401, 403),
)
def list_runs(
    cursor: str | None = None,
    use_case: UseCaseId | None = None,
    status: RunStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    ctx: Context = Depends(require(P.SETTINGS_READ)),
) -> Page[RunOut]:
    with transaction(ctx) as db:
        stmt = select(AiRun).where(AiRun.business_id == ctx.business_id)
        if use_case is not None:
            stmt = stmt.where(AiRun.use_case == use_case)
        if status is not None:
            stmt = stmt.where(AiRun.status == status)
        items, next_cursor = crud.page(db, stmt, AiRun, limit, cursor)
        return Page[RunOut](
            items=[RunOut.model_validate(r, from_attributes=True) for r in items], next_cursor=next_cursor
        )


@router.get(
    "/usage", response_model=UsageOut, summary="Today's AI usage and budget, and the last 30 days", responses=ERRORS
)
def get_usage(ctx: Context = Depends(require(P.SETTINGS_READ))) -> UsageOut:
    with transaction(ctx) as db:
        return UsageOut(today=usage.usage_today(db, ctx), days=usage.usage_days(db, ctx))


@router.post(
    "/conversations/{id}/summary",
    response_model=summary.SummaryOut,
    summary="Summarize a conversation and propose a reply",
    responses={
        **error_responses(401, 403, 404, 409, 429),
        413: {
            "description": "Input exceeds the configured token estimate",
            "content": {"application/problem+json": {}},
        },
        502: {"description": "Invalid model output"},
        503: {"description": "Provider unavailable"},
    },
)
def summarize_conversation(
    id: uuid.UUID,
    ctx: Context = Depends(require(P.ASSISTANT_USE)),
    idempotency_key: Annotated[str | None, Header(max_length=64, min_length=1)] = None,
) -> summary.SummaryOut:
    ctx.require("conversations.thread.read")
    return summary.run(ctx, id, idempotency_key=idempotency_key)


@router.get(
    "/briefing",
    response_model=briefing.BriefingOut,
    summary="Today's briefing, cached per user and UTC hour",
    responses={
        **error_responses(401, 403, 409, 429),
        502: {"description": "Invalid model output"},
        503: {"description": "Provider unavailable"},
    },
)
def get_briefing(ctx: Context = Depends(require(P.ASSISTANT_USE))) -> briefing.BriefingOut:
    return briefing.run(ctx)


@router.get(
    "/copilot/threads",
    response_model=list[copilot.CopilotThreadOut],
    responses=ERRORS,
    summary="Your latest 50 assistant threads",
)
def list_copilot_threads(ctx: Context = Depends(require(P.ASSISTANT_USE))) -> list[copilot.CopilotThreadOut]:
    return copilot.list_threads(ctx)


@router.post(
    "/copilot/threads",
    response_model=copilot.CopilotThreadOut,
    responses=error_responses(401, 403, 422),
    summary="Create an assistant thread",
)
def create_copilot_thread(
    body: copilot.ThreadCreate, ctx: Context = Depends(require(P.ASSISTANT_USE))
) -> copilot.CopilotThreadOut:
    return copilot.create_thread(ctx, body)


@router.get(
    "/copilot/threads/{id}",
    response_model=copilot.CopilotThreadDetailOut,
    responses=error_responses(401, 403, 404),
    summary="Read your assistant thread and saved messages",
)
def get_copilot_thread(
    id: uuid.UUID, ctx: Context = Depends(require(P.ASSISTANT_USE))
) -> copilot.CopilotThreadDetailOut:
    return copilot.get_thread(ctx, id)


@router.post(
    "/copilot/threads/{id}/messages",
    response_class=StreamingResponse,
    responses={**error_responses(401, 403, 404, 409, 422, 429), 200: {"content": {"text/event-stream": {}}}},
    summary="Send a staff message and stream assistant steps, proposals and the final answer",
)
def send_copilot_message(
    id: uuid.UUID,
    body: copilot.MessageCreate,
    ctx: Context = Depends(require(P.ASSISTANT_USE)),
    idempotency_key: Annotated[str | None, Header(max_length=64, min_length=1)] = None,
) -> StreamingResponse:
    prepared = copilot.prepare_message(ctx, id, body, idempotency_key=idempotency_key)
    return copilot.CopilotResponse(prepared)


@router.post(
    "/procurement/items/{item_id}/request-quotes",
    response_model=procurement.RequestQuotesOut,
    responses={
        **error_responses(401, 403, 404, 409, 429),
        502: {"description": "Invalid model output"},
        503: {"description": "Provider unavailable"},
    },
)
def request_procurement_quotes(
    item_id: uuid.UUID, ctx: Context = Depends(require(P.ASSISTANT_USE))
) -> procurement.RequestQuotesOut:
    ctx.require("purchasing.po.write")
    assert ctx.business_id is not None
    return procurement.request_quotes(item_id, ctx.business_id, ctx=ctx)


@router.post(
    "/procurement/quote-requests/{id}/extract",
    response_model=procurement.ExtractOut,
    responses={
        **error_responses(401, 403, 404, 409, 422, 429),
        413: {
            "description": "Input exceeds the configured token estimate",
            "content": {"application/problem+json": {}},
        },
        502: {"description": "Invalid model output"},
        503: {"description": "Provider unavailable"},
    },
)
def extract_procurement_reply(
    id: uuid.UUID,
    body: procurement.ExtractIn,
    ctx: Context = Depends(require(P.ASSISTANT_USE)),
    idempotency_key: Annotated[str | None, Header(max_length=64, min_length=1)] = None,
) -> procurement.ExtractOut:
    ctx.require("purchasing.po.write")
    return procurement.extract_reply(ctx, id, body, idempotency_key=idempotency_key)


@router.post(
    "/campaigns/{id}/draft",
    response_model=campaign_draft.CampaignDraftOut,
    summary="Draft an assistant campaign revision for staff to edit and submit",
    responses={
        **error_responses(401, 403, 404, 409, 422, 429),
        502: {"description": "Invalid model output"},
        503: {"description": "Provider unavailable"},
    },
)
def draft_campaign(
    id: uuid.UUID,
    body: campaign_draft.CampaignDraftIn | None = None,
    ctx: Context = Depends(require(P.ASSISTANT_USE)),
) -> campaign_draft.CampaignDraftOut:
    ctx.require("marketing.campaign.write")
    return campaign_draft.run(ctx, id, purpose=body.purpose if body is not None else None)


@router.post(
    "/copilot/threads/{thread_id}/proposals/{proposal_id}/confirm",
    response_model=approvals.ChatCard,
    summary="Explicitly confirm an action in your own chat",
    responses=error_responses(401, 403, 404, 409, 422),
)
def confirm_copilot_proposal(
    thread_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: approvals.ChatConfirmIn,
    ctx: Context = Depends(require(P.ASSISTANT_USE)),
) -> approvals.ChatCard:
    return copilot_cards.confirm(ctx, thread_id, proposal_id, body)
