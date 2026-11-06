"""/api/v1/conversations (staff) and /api/v1/public/{slug}/chat (visitors)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.modules.org import public as org
from app.platform import audit, ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import public_context, require, require_session

from . import ownership, publication, repository, service
from . import permissions as P
from .intake import limit_public_creation, require_public_intake
from .models import Conversation
from .schemas import (
    ConversationCreate,
    ConversationListItem,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    PublicChatStart,
    PublicMessageIn,
    PublicMessageOut,
    PublicThreadOut,
    StaffMessageIn,
    ThreadOut,
)
from .visitors import set_visitor_cookie

router = APIRouter()
public_router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)
VISITOR_COOKIE = "smb_visitor"


@router.post(
    "/messages/{message_id}/stop", response_model=MessageOut, summary="Stop a withheld web-chat reply", responses=ERRORS
)
def stop_reply(message_id: uuid.UUID, ctx: Context = Depends(require_session())) -> MessageOut:
    with transaction(ctx) as db:
        return MessageOut.model_validate(publication.stop(db, ctx, message_id))


class ConversationPage(BaseModel):
    items: list[ConversationListItem]
    next_cursor: str | None


def _thread(db: Session, ctx: Context, conv: Conversation) -> ThreadOut:
    names = crm.customer_names(db, ctx, {conv.customer_id} if conv.customer_id else set())
    return ThreadOut(
        **ConversationOut.model_validate(conv).model_dump(),
        customer_name=names.get(conv.customer_id) if conv.customer_id else None,
        messages=[MessageOut.model_validate(m) for m in service.messages(db, ctx, conv.id)],
    )


@router.get("", response_model=ConversationPage, summary="Inbox", responses=ERRORS)
def inbox(
    status: str | None = None,
    channel: str | None = None,
    assigned_to_me: bool = False,
    customer_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
    ctx: Context = Depends(require(P.THREAD_READ)),
) -> ConversationPage:
    with transaction(ctx) as db:
        rows, nxt = service.list_conversations(
            db,
            ctx,
            status=status,
            channel=channel,
            assigned_to_me=assigned_to_me,
            customer_id=customer_id,
            limit=limit,
            cursor=cursor,
        )
        names = crm.customer_names(db, ctx, {r.customer_id for r in rows if r.customer_id})
        prev = service.previews(db, ctx, [r.id for r in rows])
        return ConversationPage(
            items=[
                ConversationListItem(
                    **ConversationOut.model_validate(r).model_dump(),
                    customer_name=names.get(r.customer_id) if r.customer_id else None,
                    last_message_preview=prev.get(r.id, ""),
                )
                for r in rows
            ],
            next_cursor=nxt,
        )


@router.post("", response_model=ThreadOut, status_code=201, summary="Log a phone call or walk-in", responses=ERRORS)
def create(body: ConversationCreate, ctx: Context = Depends(require(P.THREAD_WRITE))) -> ThreadOut:
    with transaction(ctx) as db:
        return _thread(db, ctx, service.create_by_staff(db, ctx, body))


@router.get("/{conversation_id}", response_model=ThreadOut, summary="Thread with all messages", responses=ERRORS)
def thread(conversation_id: uuid.UUID, ctx: Context = Depends(require(P.THREAD_READ))) -> ThreadOut:
    with transaction(ctx) as db:
        result = _thread(db, ctx, repository.get_conversation(db, ctx, conversation_id))
        audit.record_view(
            db, ctx, action="conversations.conversation.viewed", target_type="conversation", target_id=conversation_id
        )
        return result


@router.patch(
    "/{conversation_id}", response_model=ThreadOut, summary="Assign, change status, link customer", responses=ERRORS
)
def patch(
    conversation_id: uuid.UUID, body: ConversationUpdate, ctx: Context = Depends(require(P.THREAD_WRITE))
) -> ThreadOut:
    with transaction(ctx) as db:
        return _thread(db, ctx, service.update_conversation(db, ctx, conversation_id, body))


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=201,
    summary="Reply or add an internal note",
    responses=ERRORS,
)
def post_message(
    conversation_id: uuid.UUID, body: StaffMessageIn, ctx: Context = Depends(require(P.THREAD_WRITE))
) -> MessageOut:
    with transaction(ctx) as db:
        return MessageOut.model_validate(service.staff_message(db, ctx, conversation_id, body.direction, body.body))


# --------------------------------------------------------------------------------------------------- visitors
def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _public_thread(db: Session, ctx: Context, conv: Conversation, cursor: str | None = None) -> PublicThreadOut:
    rows, next_cursor = service.public_messages(db, ctx, conv.id, cursor=cursor)
    return PublicThreadOut(
        conversation_id=conv.id,
        business_name=org.get_business_brief(db, ctx).name,
        ai_greeting=f"You're chatting with {org.get_business_brief(db, ctx).name}'s AI assistant.",
        next_cursor=next_cursor,
        messages=[
            PublicMessageOut(
                id=m.id,
                from_business=m.direction == "outbound",
                is_system=m.author_kind == "system",
                body=m.body,
                created_at=m.published_at or m.created_at,
            )
            for m in rows
        ],
    )


@public_router.post(
    "/{business_slug}/chat",
    response_model=PublicThreadOut,
    status_code=201,
    summary="Start a chat (visitor)",
    responses=error_responses(403, 404, 422, 429),
)
def start_chat(
    business_slug: str, body: PublicChatStart, request: Request, ctx: Context = Depends(public_context)
) -> JSONResponse:
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
    response = JSONResponse(out.model_dump(mode="json"), status_code=201)
    set_visitor_cookie(response, business_slug, token)
    return response


@public_router.get(
    "/{business_slug}/chat",
    response_model=PublicThreadOut,
    summary="My chat thread (visitor)",
    responses=error_responses(403, 404, 422, 429),
)
def my_chat(
    business_slug: str,
    request: Request,
    smb_visitor: str | None = Cookie(default=None),
    cursor: str | None = None,
    ctx: Context = Depends(public_context),
) -> PublicThreadOut:
    with transaction(ctx) as db:
        ratelimit.hit(db, f"chat-read-ip:{_client_ip(request)}", limit=120, window_seconds=60)
        conv = service.visitor_conversation(db, ctx, smb_visitor)
        ratelimit.hit(db, f"chat-read:{conv.id}", limit=60, window_seconds=60)
        return _public_thread(db, ctx, conv, cursor)


@public_router.post(
    "/{business_slug}/chat/messages",
    response_model=PublicThreadOut,
    status_code=201,
    summary="Send a chat message (visitor)",
    responses=error_responses(403, 404, 422, 429),
)
def visitor_message(
    business_slug: str,
    body: PublicMessageIn,
    request: Request,
    smb_visitor: str | None = Cookie(default=None),
    cursor: str | None = None,
    ctx: Context = Depends(public_context),
) -> PublicThreadOut:
    with transaction(ctx) as db:
        require_public_intake(db, ctx)
        conv = service.visitor_conversation(db, ctx, smb_visitor)
        ratelimit.hit(db, f"chat-msg:{conv.id}", limit=10, window_seconds=60)
        ratelimit.hit(db, f"chat-ip:{_client_ip(request)}", limit=60, window_seconds=3600)
        service.visitor_message(db, ctx, conv, body.body)
        return _public_thread(db, ctx, conv, cursor)


@router.post("/{conversation_id}/take-over", response_model=ThreadOut, responses=ERRORS)
def take_over(conversation_id: uuid.UUID, ctx: Context = Depends(require(P.THREAD_WRITE))) -> ThreadOut:
    with transaction(ctx) as db:
        return _thread(db, ctx, ownership.take_over(db, ctx, conversation_id))


@router.post("/{conversation_id}/hand-back", response_model=ThreadOut, responses=ERRORS)
def hand_back(conversation_id: uuid.UUID, ctx: Context = Depends(require(P.THREAD_WRITE))) -> ThreadOut:
    with transaction(ctx) as db:
        return _thread(db, ctx, ownership.hand_back(db, ctx, conversation_id))


@public_router.post(
    "/{business_slug}/chat/handoff", response_model=PublicThreadOut, responses=error_responses(403, 404, 409, 422, 429)
)
def visitor_handoff(
    business_slug: str,
    request: Request,
    smb_visitor: str | None = Cookie(default=None),
    ctx: Context = Depends(public_context),
) -> PublicThreadOut:
    with transaction(ctx) as db:
        conv = service.visitor_conversation(db, ctx, smb_visitor)
        ratelimit.hit(db, f"chat-msg:{conv.id}", limit=10, window_seconds=60)
        ratelimit.hit(db, f"chat-ip:{_client_ip(request)}", limit=60, window_seconds=3600)
        service.request_handoff(db, ctx, conv.id, "Visitor asked to talk to a person")
        return _public_thread(db, ctx, conv)
