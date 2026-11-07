"""Customer capability lifecycle and scheduling orchestration. No AI authorization."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.modules.notifications import public as notifications
from app.modules.org import public as org
from app.platform import audit, clock
from app.platform.config import get_settings
from app.platform.context import Context
from app.platform.errors import Conflict, Forbidden, Invalid, NotFound
from app.platform.ids import new_id
from app.platform.permissions import hash_token

from . import service
from .availability import local_label
from .models import Appointment
from .schemas import SlotQuery
from .self_service_models import SelfServiceChange, SelfServiceSession, SelfServiceToken
from .self_service_schemas import CustomerBooking, CustomerChange, CustomerReschedule, CustomerSlot

TOKEN_TTL = timedelta(minutes=20)
SESSION_TTL = timedelta(minutes=60)
COOKIE = "smb_self_service"
NEUTRAL = "If eligible bookings match that address, we'll email a sign-in link."


def customer_context(ctx: Context) -> Context:
    # Used only after capability/ownership checks; never returned to arbitrary public handlers.
    return replace(ctx, permissions=ctx.permissions | {"scheduling.appointment.read", "scheduling.appointment.write"})


def request_link(db: Session, ctx: Context, email: str, *, nonce: str) -> None:
    if not org.get_settings(db, ctx).self_service_enabled:
        return
    cid = crm.self_service_email_customer(db, ctx, email)
    if cid is None:
        return
    has_bookings = db.scalar(
        select(Appointment.id)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.customer_id == cid,
            Appointment.status != "held",
            Appointment.ends_at >= clock.now() - timedelta(days=30),
        )
        .limit(1)
    )
    if has_bookings is None:
        return
    token = secrets.token_urlsafe(32)
    assert ctx.business_id is not None
    db.add(
        SelfServiceToken(
            business_id=ctx.business_id,
            customer_id=cid,
            token_hash=hash_token(token),
            expires_at=clock.now() + TOKEN_TTL,
        )
    )
    business = org.get_business_brief(db, ctx)
    notifications.enqueue_message(
        db,
        ctx,
        channel=notifications.email_channel(),
        recipient=email,
        template_key="self_service_link",
        variables={
            "business_name": business.name,
            "link": f"{get_settings().app_base_url.rstrip('/')}/p/{business.slug}/bookings#t={token}",
        },
        idempotency_key=f"self-service-link:{ctx.business_id}:{nonce}",
        related_type="customer",
        related_id=cid,
    )
    db.flush()


def exchange(db: Session, ctx: Context, token: str, *, previous_session: str | None = None) -> str:
    if not org.get_settings(db, ctx).self_service_enabled:
        raise Forbidden("self_service.link_invalid")
    observed = db.scalar(
        select(SelfServiceToken).where(
            SelfServiceToken.business_id == ctx.business_id, SelfServiceToken.token_hash == hash_token(token)
        )
    )
    if observed is None or crm.self_service_customer(db, ctx, observed.customer_id) != "email":
        raise Forbidden("self_service.link_invalid")
    row = db.scalar(
        select(SelfServiceToken)
        .where(SelfServiceToken.business_id == ctx.business_id, SelfServiceToken.id == observed.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.used_at or row.revoked_at or row.expires_at <= clock.now():
        raise Forbidden("self_service.link_invalid")
    if previous_session:
        # Revoke the browser's previous capability before issuing a new one. A late response
        # may restore its cookie, but it must never restore access to the previous customer.
        db.execute(
            update(SelfServiceSession)
            .where(
                SelfServiceSession.business_id == ctx.business_id,
                SelfServiceSession.token_hash == hash_token(previous_session),
                SelfServiceSession.revoked_at.is_(None),
            )
            .values(revoked_at=clock.now())
        )
    row.used_at = clock.now()
    session = secrets.token_urlsafe(32)
    assert ctx.business_id is not None
    db.add(
        SelfServiceSession(
            business_id=ctx.business_id,
            customer_id=row.customer_id,
            token_hash=hash_token(session),
            expires_at=clock.now() + SESSION_TTL,
        )
    )
    db.flush()
    return session


def authenticate(db: Session, ctx: Context, token: str | None) -> uuid.UUID:
    if not token or not org.get_settings(db, ctx).self_service_enabled:
        raise Forbidden("self_service.session_invalid")
    observed = db.scalar(
        select(SelfServiceSession).where(
            SelfServiceSession.business_id == ctx.business_id, SelfServiceSession.token_hash == hash_token(token)
        )
    )
    if observed is None or crm.self_service_customer(db, ctx, observed.customer_id) != "email":
        raise Forbidden("self_service.session_invalid")
    row = db.scalar(
        select(SelfServiceSession)
        .where(SelfServiceSession.business_id == ctx.business_id, SelfServiceSession.id == observed.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.revoked_at or row.expires_at <= clock.now():
        raise Forbidden("self_service.session_invalid")
    row.expires_at = clock.now() + SESSION_TTL
    return row.customer_id


def owned(
    db: Session, ctx: Context, cid: uuid.UUID, appointment_id: uuid.UUID, version: int | None = None
) -> Appointment:
    row = db.scalar(
        select(Appointment)
        .where(
            Appointment.business_id == ctx.business_id, Appointment.id == appointment_id, Appointment.customer_id == cid
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("scheduling.appointment_not_found")
    if version is not None and row.version != version:
        raise Conflict("scheduling.appointment_version_conflict")
    if row.status != "confirmed" or row.starts_at <= clock.now():
        raise Conflict("self_service.booking_unavailable")
    return row


def project(db: Session, ctx: Context, row: Appointment) -> CustomerBooking:
    settings = org.get_settings(db, ctx)
    # Deliberately omit internal resource names; no public-name flag exists on this branch.
    details = service.enrich(db, ctx, [row])[0]
    inside = row.starts_at < clock.now() + timedelta(hours=settings.cancellation_notice_hours)
    return CustomerBooking(
        id=row.id,
        service_name=details["service_name"],
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        timezone=row.timezone,
        local_time=local_label(row.starts_at, ZoneInfo(row.timezone)),
        location=org.get_location(db, ctx, row.location_id).name,
        status=row.status,
        version=row.version,
        fee_required=inside and bool(settings.cancellation_fee_text),
        fee_text=settings.cancellation_fee_text if inside else None,
    )


def bookings(db: Session, ctx: Context, cid: uuid.UUID) -> list[CustomerBooking]:
    rows = db.scalars(
        select(Appointment)
        .where(
            Appointment.business_id == ctx.business_id,
            Appointment.customer_id == cid,
            Appointment.starts_at > clock.now(),
            Appointment.status != "held",
        )
        .order_by(Appointment.starts_at)
    )
    return [project(db, ctx, row) for row in rows]


def slots(
    db: Session, ctx: Context, cid: uuid.UUID, appointment_id: uuid.UUID, earliest: object, latest: object
) -> list[CustomerSlot]:
    row = owned(db, ctx, cid, appointment_id)
    query = SlotQuery.model_validate(
        {"service_id": row.service_id, "location_id": row.location_id, "earliest": earliest, "latest": latest}
    )
    return [CustomerSlot(**slot.model_dump(exclude={"resource_name"})) for slot in service.find_slots(db, ctx, query)]


def change(
    db: Session, ctx: Context, cid: uuid.UUID, appointment_id: uuid.UUID, body: CustomerChange, *, reschedule: bool
) -> CustomerBooking:
    ctx = customer_context(ctx)
    row = owned(db, ctx, cid, appointment_id, body.version)
    settings = org.get_settings(db, ctx)
    inside = row.starts_at < clock.now() + timedelta(hours=settings.cancellation_notice_hours)
    fee = settings.cancellation_fee_text if inside else None
    if fee and not body.fee_acknowledged:
        raise Invalid("self_service.fee_acknowledgment_required")
    if fee and body.acknowledged_fee_text != fee:
        raise Conflict("self_service.fee_changed")
    if reschedule:
        assert isinstance(body, CustomerReschedule)
        # Same service and location. Selection is revalidated by the canonical slot finder and hold path.
        available = service.find_slots(
            db,
            ctx,
            SlotQuery(
                service_id=row.service_id,
                location_id=row.location_id,
                resource_id=body.resource_id,
                earliest=body.starts_at,
                latest=body.starts_at + timedelta(seconds=1),
            ),
        )
        if not any(s.starts_at == body.starts_at and s.resource_id == body.resource_id for s in available):
            raise Conflict("scheduling.slot_unavailable")
        hold = service.place_hold(
            db, ctx, service_id=row.service_id, resource_id=body.resource_id, starts_at=body.starts_at, customer_id=cid
        )
        # Consume the transactional hold before updating the original appointment; retain id and stock.
        db.delete(hold)
        db.flush()
        row = service.reschedule(db, ctx, row.id, body.version, body.starts_at, body.resource_id)
        action = "reschedule"
    else:
        row = service.cancel(db, ctx, row.id, body.version, "by customer via self-service")
        action = "cancel"
    assert ctx.business_id is not None
    receipt = SelfServiceChange(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=cid,
        appointment_id=row.id,
        action=action,
        appointment_version=row.version,
        fee_acknowledged=bool(fee and body.fee_acknowledged),
        fee_text=fee,
        acknowledged_at=clock.now() if fee and body.fee_acknowledged else None,
    )
    db.add(receipt)
    summary = f"Booking {action} by customer via self-service"
    crm.add_timeline_entry(
        db,
        ctx,
        crm.TimelineEntryIn(
            customer_id=cid,
            occurred_at=clock.now(),
            kind="appointment",
            ref_module="scheduling",
            ref_type="self_service_change",
            ref_id=receipt.id,
            summary=summary,
        ),
    )
    audit.record(
        db,
        ctx,
        action=f"scheduling.self_service.{action}",
        target_type="appointment",
        target_id=row.id,
        summary=summary,
        changes={"fee_acknowledged": receipt.fee_acknowledged, "fee_text": fee, "appointment_version": row.version},
    )
    notifications.notify_permission(
        db,
        ctx,
        "scheduling.appointment.read",
        kind="self_service_change",
        title=summary,
        body="Review the updated booking.",
        link="/schedule",
    )
    email = crm.get_customer_contact(db, ctx, cid, "email")
    if email:
        notifications.enqueue_message(
            db,
            ctx,
            channel=notifications.email_channel(),
            recipient=email,
            template_key="self_service_confirmation",
            variables={
                "business_name": org.get_business_brief(db, ctx).name,
                "action": action,
                "local_time": local_label(row.starts_at, ZoneInfo(row.timezone)),
            },
            idempotency_key=f"self-service-change:{receipt.id}",
            related_type="customer",
            related_id=cid,
        )
    db.flush()
    return project(db, ctx, row)


def revoke(db: Session, ctx: Context, customer_id: uuid.UUID) -> None:
    for model in (SelfServiceToken, SelfServiceSession):
        db.execute(
            update(model)
            .where(model.business_id == ctx.business_id, model.customer_id == customer_id, model.revoked_at.is_(None))
            .values(revoked_at=clock.now())
        )
