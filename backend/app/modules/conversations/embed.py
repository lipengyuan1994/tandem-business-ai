"""Cookie-independent iframe chat; same services/projection and quotas as first-party chat."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.platform import ratelimit
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Forbidden
from app.platform.permissions import public_context

from . import service
from .api import _client_ip, _public_thread
from .intake import limit_public_creation, require_public_intake
from .schemas import PublicChatStart, PublicMessageIn, PublicThreadOut

router = APIRouter()


def require_embed_origin(request: Request) -> None:
    # The request originates inside our iframe, never at the embedding shop's origin.
    if request.headers.get("origin") != get_settings().app_base_url.rstrip("/"):
        raise Forbidden("auth.csrf_failed")


@router.post("/{business_slug}/embed/chat", response_model=PublicThreadOut, status_code=201)
def start(body: PublicChatStart, request: Request, ctx: Context = Depends(public_context)) -> JSONResponse:
    require_embed_origin(request)
    with transaction(ctx) as db:
        require_public_intake(db, ctx)
        limit_public_creation(db, _client_ip(request))
        conv, _ = service.start_conversation(
            db,
            ctx,
            channel="web_chat",
            body=body.message,
            author_kind="visitor",
            visitor_name=body.name,
            visitor_email=str(body.email) if body.email else None,
            visitor_phone=body.phone,
            consent_to_contact=body.consent_to_contact,
        )
        token = service.issue_visitor_token(db, ctx, conv.id)
        out = _public_thread(db, ctx, conv)
    return JSONResponse(
        out.model_dump(mode="json"), status_code=201, headers={"X-Embed-Token": token, "Cache-Control": "no-store"}
    )


@router.get("/{business_slug}/embed/chat", response_model=PublicThreadOut)
def read(
    request: Request,
    cursor: str | None = None,
    x_embed_token: str | None = Header(default=None),
    ctx: Context = Depends(public_context),
) -> PublicThreadOut:
    with transaction(ctx) as db:
        ratelimit.hit(db, f"chat-read-ip:{_client_ip(request)}", limit=120, window_seconds=60)
        conv = service.visitor_conversation(db, ctx, x_embed_token)
        ratelimit.hit(db, f"chat-read:{conv.id}", limit=60, window_seconds=60)
        return _public_thread(db, ctx, conv, cursor)


@router.post("/{business_slug}/embed/chat/messages", response_model=PublicThreadOut, status_code=201)
def message(
    body: PublicMessageIn,
    request: Request,
    cursor: str | None = None,
    x_embed_token: str | None = Header(default=None),
    ctx: Context = Depends(public_context),
) -> PublicThreadOut:
    require_embed_origin(request)
    with transaction(ctx) as db:
        require_public_intake(db, ctx)
        conv = service.visitor_conversation(db, ctx, x_embed_token)
        ratelimit.hit(db, f"chat-msg:{conv.id}", limit=10, window_seconds=60)
        ratelimit.hit(db, f"chat-ip:{_client_ip(request)}", limit=60, window_seconds=3600)
        service.visitor_message(db, ctx, conv, body.body)
        return _public_thread(db, ctx, conv, cursor)


@router.post("/{business_slug}/embed/chat/handoff", response_model=PublicThreadOut)
def handoff(
    request: Request, x_embed_token: str | None = Header(default=None), ctx: Context = Depends(public_context)
) -> PublicThreadOut:
    require_embed_origin(request)
    with transaction(ctx) as db:
        conv = service.visitor_conversation(db, ctx, x_embed_token)
        ratelimit.hit(db, f"chat-msg:{conv.id}", limit=10, window_seconds=60)
        ratelimit.hit(db, f"chat-ip:{_client_ip(request)}", limit=60, window_seconds=3600)
        service.request_handoff(db, ctx, conv.id, "Visitor asked to talk to a person")
        return _public_thread(db, ctx, conv)
