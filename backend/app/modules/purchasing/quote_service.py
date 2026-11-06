"""Transactional, non-AI procurement writes shared by executors and the signed supplier form."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.platform import audit, clock, events
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid

from . import permissions as P
from . import quote_reads as reads
from . import quote_repository as repo
from . import repository as po_repo
from . import service, tokens
from .events import QuoteReceived
from .models import (
    PurchaseOrder,
    QuoteRequest,
    QuoteRequestLine,
    QuoteRequestSupplier,
    SupplierQuote,
    SupplierQuoteLine,
)
from .schemas import ConversionSnapshot, POCreate, POLineIn
from .schemas_quotes import CreatePOFromQuote, POAbout, RecordSupplierQuote, SendQuoteRequests, SupplierFollowup


def record_audit(db: Session, ctx: Context, row_id: uuid.UUID, action: str) -> None:
    audit.record(
        db,
        ctx,
        action=f"purchasing.{action}",
        target_type="quote_request",
        target_id=row_id,
        summary=action.replace("_", " "),
    )


def expiry(db: Session, ctx: Context, reply_by: date) -> datetime:
    # The entire reply_by + 7th local day is valid, including DST transitions.
    zone = ZoneInfo(org.get_business_brief(db, ctx).timezone)
    return datetime.combine(reply_by + timedelta(days=8), time.min, zone).astimezone(UTC)


def quote_link(token: str) -> str:
    """The supplier's public quote page, built one way for the e-mail and for staff who copy it (https)."""
    return f"https://{urlsplit(get_settings().app_base_url).netloc}/q/{token}"


def validate_send(db: Session, ctx: Context, p: SendQuoteRequests) -> None:
    if p.reply_by < reads.today(db, ctx):
        raise Invalid("purchasing.reply_by_passed")
    service.prepare_lines(
        db, ctx, [POLineIn(catalog_item_id=r.item_id, quantity=r.quantity, unit_cost_minor=0) for r in p.lines]
    )
    for supplier_id in p.supplier_ids:
        if not po_repo.supplier(db, ctx, supplier_id).email:
            raise Invalid("purchasing.supplier_email_required")
    base = urlsplit(get_settings().app_base_url)
    if base.scheme != "https" or not base.netloc:
        raise Invalid("purchasing.https_base_url_required")


def send_requests(db: Session, ctx: Context, p: SendQuoteRequests, key: str) -> QuoteRequest:
    ctx.require(P.APPROVE)
    po_repo.lock_idempotency(db, ctx, key)
    request_id = uuid.uuid5(uuid.NAMESPACE_URL, f"purchasing.quote:{ctx.business_id}:{key}")
    existing = db.scalar(
        select(QuoteRequest).where(QuoteRequest.business_id == ctx.business_id, QuoteRequest.id == request_id)
    )
    if existing is not None:
        return existing
    validate_send(db, ctx, p)
    row = QuoteRequest(
        id=request_id,
        business_id=ctx.business_id,
        status="sent",
        needed_by=p.needed_by,
        reply_by=p.reply_by,
        message=p.message,
        created_by=ctx.user_id or ctx.on_behalf_of_user_id,
    )
    db.add(row)
    db.flush()
    db.add_all(
        QuoteRequestLine(business_id=ctx.business_id, request_id=row.id, **line.model_dump()) for line in p.lines
    )
    assert ctx.business_id is not None
    brief = org.get_business_brief(db, ctx)
    for supplier_id in p.supplier_ids:
        supplier = po_repo.supplier(db, ctx, supplier_id)
        token = tokens.make_token(ctx.business_id, row.id, supplier.id, expiry(db, ctx, row.reply_by))
        db.add(
            QuoteRequestSupplier(
                business_id=ctx.business_id,
                request_id=row.id,
                supplier_id=supplier.id,
                token_hash=tokens.token_hash(token),
                status="invited",
                sent_at=clock.now(),
            )
        )
        notifications.enqueue_message(
            db,
            ctx,
            channel=notifications.email_channel(),
            recipient=supplier.email,
            template_key="quote_request",
            variables={
                "business_name": brief.name,
                "body": p.message or "",
                "needed_by": str(p.needed_by),
                "reply_by": str(p.reply_by),
                "quote_url": quote_link(token),
                "lines": "\n".join(f"{line.name}: {line.quantity}" for line in reads.request_lines(db, ctx, row.id)),
            },
            idempotency_key=f"quote-request:{ctx.business_id}:{row.id}:{supplier.id}",
            related_type="quote_request",
            related_id=row.id,
        )
    db.flush()
    record_audit(db, ctx, row.id, "quote_requests_sent")
    return row


def copy_link(db: Session, ctx: Context, request_id: uuid.UUID, supplier_id: uuid.UUID) -> str:
    """The signed page of a supplier who has not answered, for staff to hand over when no e-mail can (the demo).

    The invite stores only the token's hash, and the e-mail's expiry is not stored either: it follows from the request's
    reply_by and the business time zone (`expiry`). The same token is rebuilt from those and must match the stored hash.
    If it does not (the secret or the time zone changed since sending) the link is refused, never guessed.
    The audit event names the request and supplier, not the link.
    """
    ctx.require(P.WRITE)
    assert ctx.business_id is not None
    request = repo.request(db, ctx, request_id)
    invite = repo.invite(db, ctx, request.id, supplier_id)
    if request.status != "sent":
        raise Conflict("purchasing.quote_request_closed")
    valid_until = expiry(db, ctx, request.reply_by)
    if invite.status != "invited" or clock.now() >= valid_until:
        raise Conflict("purchasing.invite_not_unanswered")
    token = tokens.make_token(ctx.business_id, request.id, supplier_id, valid_until)
    if not hmac.compare_digest(invite.token_hash, tokens.token_hash(token)):
        raise Conflict("purchasing.quote_link_unavailable")
    audit.record(
        db,
        ctx,
        action="purchasing.quote_link.copied",
        target_type="quote_request",
        target_id=request.id,
        summary="Supplier quote link copied",
        changes={"supplier_id": str(supplier_id)},
    )
    return quote_link(token)


def validate_record(db: Session, ctx: Context, p: RecordSupplierQuote) -> None:
    request = repo.request(db, ctx, p.quote_request_id)
    invite = repo.invite(db, ctx, request.id, p.supplier_id)
    if request.status != "sent" or (
        p.source == "form" and (invite.status == "expired" or clock.now() >= expiry(db, ctx, request.reply_by))
    ):
        raise Conflict("purchasing.quote_request_closed")
    requested = {r.item_id for r in repo.lines(db, ctx, request.id)}
    if not {r.item_id for r in p.lines} <= requested:
        raise Invalid("purchasing.quote_item_not_requested")
    existing = next((q for q in repo.quotes(db, ctx, request.id) if q.supplier_id == p.supplier_id), None)
    if existing is not None and existing.status == "accepted":
        raise Conflict("purchasing.quote_accepted")


def answered_event(db: Session, ctx: Context, request_id: uuid.UUID, supplier_id: uuid.UUID, *, declined: bool) -> None:
    db.flush()
    source = approvals.workflow_for_result(db, ctx, "quote_request_id", request_id)
    if source is not None:
        ctx = replace(ctx, **source)
    events.publish(
        db,
        ctx,
        QuoteReceived(
            quote_request_id=request_id,
            supplier_id=supplier_id,
            declined=declined,
            all_answered=reads.all_answered(db, ctx, request_id),
        ),
    )


def record_quote(db: Session, ctx: Context, p: RecordSupplierQuote, *, from_form: bool = False) -> SupplierQuote:
    if not from_form:
        ctx.require(P.WRITE)
    elif ctx.actor_kind != "visitor" or p.source != "form":
        raise Invalid("purchasing.invalid_quote_source")
    repo.request(db, ctx, p.quote_request_id, lock=True)
    validate_record(db, ctx, p)
    invite = repo.invite(db, ctx, p.quote_request_id, p.supplier_id)
    row = next((q for q in repo.quotes(db, ctx, p.quote_request_id) if q.supplier_id == p.supplier_id), None)
    values = p.model_dump(exclude={"quote_request_id", "supplier_id", "lines"})
    if row is None:
        row = SupplierQuote(
            business_id=ctx.business_id, request_id=p.quote_request_id, supplier_id=p.supplier_id, **values
        )
        db.add(row)
        db.flush()
    else:
        for key, value in values.items():
            setattr(row, key, value)
        row.status = "received"
        row.version += 1
        db.execute(
            delete(SupplierQuoteLine).where(
                SupplierQuoteLine.business_id == ctx.business_id, SupplierQuoteLine.quote_id == row.id
            )
        )
    db.add_all(SupplierQuoteLine(business_id=ctx.business_id, quote_id=row.id, **line.model_dump()) for line in p.lines)
    invite.status, invite.responded_at = "responded", clock.now()
    invite.version += 1
    answered_event(db, ctx, p.quote_request_id, p.supplier_id, declined=False)
    record_audit(db, ctx, p.quote_request_id, "quote_recorded")
    return row


def token_invite(db: Session, ctx: Context, token: str, *, lock: bool = False) -> QuoteRequestSupplier:
    business_id, request_id, supplier_id = tokens.read_token(token)
    if business_id != ctx.business_id:
        raise Invalid("purchasing.invalid_quote_token")
    request = repo.request(db, ctx, request_id, lock=lock)
    invite = repo.invite(db, ctx, request.id, supplier_id)
    if not hmac.compare_digest(invite.token_hash, tokens.token_hash(token)):
        raise Invalid("purchasing.invalid_quote_token")
    if clock.now() >= expiry(db, ctx, request.reply_by) or invite.status == "expired":
        raise Invalid("purchasing.invalid_quote_token")
    if request.status != "sent":
        raise Conflict("purchasing.quote_request_closed")
    return invite


def decline(db: Session, ctx: Context, invite: QuoteRequestSupplier) -> None:
    # The endpoint already holds the request lock and has verified this invite's capability.
    for q in repo.quotes(db, ctx, invite.request_id):
        if q.supplier_id == invite.supplier_id:
            q.status = "rejected"
            q.version += 1
    invite.status, invite.responded_at = "declined", clock.now()
    invite.version += 1
    answered_event(db, ctx, invite.request_id, invite.supplier_id, declined=True)
    record_audit(db, ctx, invite.request_id, "quote_declined")


def quote_po_data(db: Session, ctx: Context, quote: SupplierQuote) -> POCreate:
    prices = {r.item_id: r.unit_price_minor for r in repo.quote_lines(db, ctx, quote.id)}
    return POCreate(
        supplier_id=quote.supplier_id,
        notes=quote.notes,
        lines=[
            POLineIn(catalog_item_id=r.item_id, quantity=r.quantity, unit_cost_minor=prices[r.item_id])
            for r in repo.lines(db, ctx, quote.request_id)
        ],
    )


def validate_po(db: Session, ctx: Context, p: CreatePOFromQuote) -> None:
    quote = repo.quote(db, ctx, p.quote_id)
    if quote.version != p.quote_version:
        raise Conflict("purchasing.quote_version_conflict", current_version=quote.version)
    request = repo.request(db, ctx, quote.request_id)
    invite = repo.invite(db, ctx, request.id, quote.supplier_id)
    if quote.status != "received" or request.status != "sent" or invite.status != "responded":
        raise Conflict("purchasing.quote_unavailable")
    comparison = reads.compare_quotes(db, ctx, request.id)
    if not any(q.quote_id == quote.id for q in comparison.ranked):
        raise Invalid("purchasing.quote_ineligible")
    # Apply the PO's line and aggregate bounds during proposal creation, as well as execution.
    service.prepare_lines(db, ctx, quote_po_data(db, ctx, quote).lines)


def po_from_quote(db: Session, ctx: Context, p: CreatePOFromQuote) -> uuid.UUID:
    ctx.require(P.APPROVE)
    quote = repo.quote(db, ctx, p.quote_id)
    request = repo.request(db, ctx, quote.request_id, lock=True)
    # Refresh after waiting for another writer holding the request lock.
    db.refresh(quote)
    validate_po(db, ctx, p)
    data = quote_po_data(db, ctx, quote)
    values = service.prepare_lines(db, ctx, data.lines)
    expected = ConversionSnapshot(
        supplier_name=po_repo.supplier(db, ctx, quote.supplier_id).name,
        currency=quote.currency,
        notes=quote.notes,
        lines=values,
        total_minor=sum(line.line_total_minor for line in values),
    )
    row = service.create(db, ctx, data, expected=expected)
    row.source_quote_id = quote.id
    quote.status, quote.version = "accepted", quote.version + 1
    request.status, request.version = "closed", request.version + 1
    service.record(db, ctx, row, "from_quote")
    return row.id


def validate_followup(db: Session, ctx: Context, p: SupplierFollowup) -> None:
    supplier = po_repo.supplier(db, ctx, p.supplier_id)
    if not supplier.email:
        raise Invalid("purchasing.supplier_email_required")
    if isinstance(p.about, POAbout):
        po = po_repo.order(db, ctx, p.about.purchase_order_id)
        if po.supplier_id != supplier.id:
            raise Invalid("purchasing.followup_supplier_mismatch")
        if po.status not in ("sent", "partially_received"):
            raise Conflict("purchasing.invalid_transition")
    else:
        request = repo.request(db, ctx, p.about.quote_request_id)
        invite = repo.invite(db, ctx, request.id, supplier.id)
        if request.status != "sent" or invite.status != "invited" or clock.now() >= expiry(db, ctx, request.reply_by):
            raise Conflict("purchasing.invite_not_unanswered")


def followup(db: Session, ctx: Context, p: SupplierFollowup, key: str) -> uuid.UUID:
    ctx.require(P.WRITE)
    row: PurchaseOrder | QuoteRequestSupplier
    if isinstance(p.about, POAbout):
        row = po_repo.order(db, ctx, p.about.purchase_order_id, lock=True)
        db.refresh(row)
        target_type = "purchase_order"
    else:
        repo.request(db, ctx, p.about.quote_request_id, lock=True)
        row = repo.invite(db, ctx, p.about.quote_request_id, p.supplier_id)
        target_type = "quote_request"
    validate_followup(db, ctx, p)
    supplier = po_repo.supplier(db, ctx, p.supplier_id)
    target_id = p.about.purchase_order_id if isinstance(p.about, POAbout) else p.about.quote_request_id
    outbox_id = notifications.enqueue_message(
        db,
        ctx,
        channel=notifications.email_channel(),
        recipient=supplier.email,
        template_key="supplier_followup",
        variables={"business_name": org.get_business_brief(db, ctx).name, "body": p.body},
        idempotency_key=f"supplier-followup:{ctx.business_id}:{key}",
        related_type=target_type,
        related_id=target_id,
    )
    row.last_followup_at = clock.now()
    row.version += 1
    record_audit(db, ctx, target_id, "supplier_followup_sent")
    return outbox_id


def expire_invites(db: Session, ctx: Context, payload: dict[str, Any]) -> None:
    day = reads.today(db, ctx)
    requests = list(
        db.scalars(
            select(QuoteRequest)
            .where(QuoteRequest.business_id == ctx.business_id, QuoteRequest.reply_by < day - timedelta(days=7))
            .order_by(QuoteRequest.id)
            .with_for_update()
        )
    )
    for request in requests:
        for invite in repo.invites(db, ctx, request.id):
            if invite.status == "invited":
                invite.status, invite.version = "expired", invite.version + 1
    db.flush()
