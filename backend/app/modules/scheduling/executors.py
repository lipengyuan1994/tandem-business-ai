"""Executors owned by scheduling. book_appointment is the flagship composite action (07-workflow-engine.md#holds)."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.modules.catalog import public as catalog
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.inventory import public as inventory
from app.modules.org import public as org
from app.platform import crud
from app.platform.context import Context
from app.platform.errors import Conflict
from app.platform.quantities import format_quantity

from . import availability as av
from . import permissions as P
from . import service
from .models import Appointment, Resource
from .schemas import BookAppointmentPayload, BookingRequest, CancelPayload, ExistingCustomer, ReschedulePayload


def _tz(db: Session, ctx: Context) -> ZoneInfo:
    return ZoneInfo(org.get_default_location(db, ctx).timezone)


def _describe_book(db: Session, ctx: Context, p: BookAppointmentPayload) -> list[PreviewLine]:
    req = catalog.get_service_requirements(db, ctx, p.service_id)
    resource = crud.get_scoped(db, ctx, Resource, p.resource_id)
    if isinstance(p.customer, ExistingCustomer):
        who = crm.get_customer_summary(db, ctx, p.customer.id).display_name
        lines = [PreviewLine("Customer", None, who)]
    else:
        lines = [PreviewLine("New customer", None, p.customer.new.display_name)]
    lines.append(
        PreviewLine(
            "Appointment",
            None,
            f"{av.local_label(p.starts_at, _tz(db, ctx))} · {req.name} · {resource.name if resource else '?'}",
        )
    )
    stock = {a.item_id: a for a in inventory.get_availability(db, ctx, [part.item_id for part in req.parts])}
    for part in req.parts:
        a = stock.get(part.item_id)
        lines.append(
            PreviewLine(
                f"Reserve {part.item_name}",
                f"{format_quantity(a.available)} available" if a else None,
                f"{format_quantity(a.available - part.quantity) if a else '?'} available "
                f"(−{format_quantity(part.quantity)})",
            )
        )
    if p.reply:
        lines.append(PreviewLine("Reply to customer", None, p.reply.body))
    return lines


def _validate_book(db: Session, ctx: Context, p: BookAppointmentPayload) -> None:
    catalog.get_service_requirements(db, ctx, p.service_id)
    crud.require_scoped(db, ctx, Resource, p.resource_id, "scheduling.resource_not_found")
    if p.hold_id is None and not service._offered(db, ctx, p.service_id, p.resource_id, p.starts_at):
        raise Conflict("scheduling.slot_unavailable")


def _run_book(db: Session, ctx: Context, p: BookAppointmentPayload, meta: ExecutionMeta) -> dict[str, Any]:
    if ctx.ai_run_id is not None and p.reply:
        # Ownership changes (including emergency escalation) invalidate an AI booking before any write.
        conversations.ensure_ai_active(db, ctx, p.reply.conversation_id, lock=True)
    customer_id = (
        p.customer.id if isinstance(p.customer, ExistingCustomer) else crm.create_customer(db, ctx, p.customer.new).id
    )
    if p.reply:
        conversations.lock_for_customer(db, ctx, p.reply.conversation_id, customer_id)
    appointment = service.book(
        db,
        ctx,
        BookingRequest(
            customer_id=customer_id,
            service_id=p.service_id,
            resource_id=p.resource_id,
            starts_at=p.starts_at,
            hold_id=p.hold_id,
            notes=p.notes,
        ),
    )
    result: dict[str, Any] = {"appointment_id": str(appointment.id), "customer_id": str(customer_id)}
    if p.reply:
        conversations.link_customer(db, ctx, p.reply.conversation_id, customer_id)
        result["message_id"] = str(
            conversations.send_reply(db, ctx, p.reply.conversation_id, p.reply.body, proposal_id=meta.proposal_id).id
        )
    return result


def _appt_line(db: Session, ctx: Context, appointment_id: Any) -> tuple[Appointment | None, str]:
    a = crud.get_scoped(db, ctx, Appointment, appointment_id)
    return a, (av.local_label(a.starts_at, ZoneInfo(a.timezone)) if a else "?")


def _describe_reschedule(db: Session, ctx: Context, p: ReschedulePayload) -> list[PreviewLine]:
    _, before = _appt_line(db, ctx, p.appointment_id)
    lines = [PreviewLine("Appointment time", before, av.local_label(p.new_starts_at, _tz(db, ctx)))]
    return lines + ([PreviewLine("Reply to customer", None, p.reply.body)] if p.reply else [])


def _validate_reschedule(db: Session, ctx: Context, p: ReschedulePayload) -> None:
    a = crud.require_scoped(db, ctx, Appointment, p.appointment_id, "scheduling.appointment_not_found")
    if a.version != p.appointment_version:
        raise Conflict("scheduling.appointment_version_conflict", current_version=a.version)


def _run_reschedule(db: Session, ctx: Context, p: ReschedulePayload, meta: ExecutionMeta) -> dict[str, Any]:
    a = service.reschedule(db, ctx, p.appointment_id, p.appointment_version, p.new_starts_at, p.resource_id)
    if p.reply:
        conversations.send_reply(db, ctx, p.reply.conversation_id, p.reply.body, proposal_id=meta.proposal_id)
    return {"appointment_id": str(a.id)}


def _describe_cancel(db: Session, ctx: Context, p: CancelPayload) -> list[PreviewLine]:
    _, when = _appt_line(db, ctx, p.appointment_id)
    lines = [PreviewLine("Cancel appointment", when, f"cancelled ({p.reason}); reserved parts are released")]
    return lines + ([PreviewLine("Reply to customer", None, p.reply.body)] if p.reply else [])


def _run_cancel(db: Session, ctx: Context, p: CancelPayload, meta: ExecutionMeta) -> dict[str, Any]:
    a = service.cancel(db, ctx, p.appointment_id, p.appointment_version, p.reason)
    if p.reply:
        conversations.send_reply(db, ctx, p.reply.conversation_id, p.reply.body, proposal_id=meta.proposal_id)
    return {"appointment_id": str(a.id)}


BOOK = ExecutorSpec(
    "scheduling.book_appointment", BookAppointmentPayload, P.PROPOSAL_APPROVE, _describe_book, _validate_book, _run_book
)
RESCHEDULE = ExecutorSpec(
    "scheduling.reschedule_appointment",
    ReschedulePayload,
    P.PROPOSAL_APPROVE,
    _describe_reschedule,
    _validate_reschedule,
    _run_reschedule,
)


def _validate_cancel(db: Session, ctx: Context, p: CancelPayload) -> None:
    a = crud.require_scoped(db, ctx, Appointment, p.appointment_id, "scheduling.appointment_not_found")
    if a.version != p.appointment_version:
        raise Conflict("scheduling.appointment_version_conflict", current_version=a.version)


CANCEL = ExecutorSpec(
    "scheduling.cancel_appointment", CancelPayload, P.PROPOSAL_APPROVE, _describe_cancel, _validate_cancel, _run_cancel
)
