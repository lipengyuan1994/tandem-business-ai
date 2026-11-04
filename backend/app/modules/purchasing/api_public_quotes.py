"""Capability-protected supplier form. No login or tenant selector; token selects the tenant."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform import ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses

from . import quote_reads as reads
from . import quote_repository as repo
from . import quote_service as service
from . import tokens
from .schemas_quotes import DeclineQuote, PublicQuoteInput, PublicQuoteRequest, RecordSupplierQuote

router = APIRouter()
ERRORS = error_responses(404, 409, 422, 429)


def quote_context(token: str, request: Request) -> Context:
    business_id, _, _ = tokens.read_token(token)
    return Context(
        request_id=getattr(request.state, "request_id", "-"),
        actor_kind="visitor",
        user_id=None,
        business_id=business_id,
        permissions=frozenset({"public"}),
    )


quote_context.smb_permission = "public"  # type: ignore[attr-defined]


@router.get(
    "/{token}", response_model=PublicQuoteRequest, summary="Read a signed supplier quote invitation", responses=ERRORS
)
def get_quote(token: str, request: Request, ctx: Context = Depends(quote_context)) -> PublicQuoteRequest:
    with transaction(ctx) as db:
        ip = request.client.host if request.client else "unknown"
        ratelimit.hit(db, f"quote-read-ip:{ip}", limit=120, window_seconds=60)
        invite = service.token_invite(db, ctx, token)
        ratelimit.hit(db, f"quote-read:{invite.id}", limit=60, window_seconds=60)
        row = repo.request(db, ctx, invite.request_id)
        return PublicQuoteRequest(
            business_name=org.get_business_brief(db, ctx).name,
            currency=catalog.get_default_currency(db, ctx),
            needed_by=row.needed_by,
            reply_by=row.reply_by,
            message=row.message,
            lines=reads.request_lines(db, ctx, row.id),
        )


@router.post("/{token}", response_model=dict[str, str], summary="Submit or decline a supplier quote", responses=ERRORS)
def post_quote(
    token: str, body: DeclineQuote | PublicQuoteInput, request: Request, ctx: Context = Depends(quote_context)
) -> dict[str, str]:
    with transaction(ctx) as db:
        ip = request.client.host if request.client else "unknown"
        ratelimit.hit(db, f"quote-post-ip:{ip}", limit=60, window_seconds=3600)
        invite = service.token_invite(db, ctx, token, lock=True)
        ratelimit.hit(db, f"quote-post:{invite.id}", limit=10, window_seconds=60)
        if isinstance(body, DeclineQuote):
            service.decline(db, ctx, invite)
            return {"status": "declined"}
        service.record_quote(
            db,
            ctx,
            RecordSupplierQuote(
                **body.model_dump(exclude={"declined"}),
                quote_request_id=invite.request_id,
                supplier_id=invite.supplier_id,
                currency=catalog.get_default_currency(db, ctx),
                source="form",
            ),
            from_form=True,
        )
        return {"status": "responded"}
