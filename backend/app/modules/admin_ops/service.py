"""Operational facts for the System page. Per-business numbers only; nothing here reveals another business."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.modules.notifications import public as notifications
from app.platform import audit, clock
from app.platform.config import get_settings, is_regulated
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.errors import Conflict, Invalid
from app.platform.export import dump_models
from app.platform.ids import new_id
from app.platform.models import DomainEvent, EventDelivery, IdempotencyKey, Job
from app.platform.registry import ErasureTarget, get_registry
from app.platform.smtp import parse_smtp_url

from . import permissions as P
from .models import ErasureRequest
from .schemas import BackupStatus, EmailStatus, OutboxStatus, QueueStatus, SystemStatus


def _queue(db: Session, ctx: Context) -> QueueStatus:
    counts = {
        status: int(n)
        for status, n in db.execute(
            select(Job.status, func.count()).where(Job.business_id == ctx.business_id).group_by(Job.status)
        )
    }
    oldest = db.scalar(
        select(func.min(Job.run_at)).where(
            Job.business_id == ctx.business_id, Job.status == "queued", Job.run_at <= clock.now()
        )
    )
    dead_kinds = list(
        db.scalars(
            select(Job.kind)
            .where(Job.business_id == ctx.business_id, Job.status == "dead")
            .group_by(Job.kind)
            .order_by(func.max(Job.updated_at).desc())
            .limit(5)
        )
    )
    deliveries_dead = db.scalar(
        select(func.count())
        .select_from(EventDelivery)
        .join(DomainEvent, DomainEvent.id == EventDelivery.event_id)
        .where(DomainEvent.business_id == ctx.business_id, EventDelivery.status == "dead")
    )
    return QueueStatus(
        counts=counts,
        oldest_queued_age_seconds=int((clock.now() - oldest).total_seconds()) if oldest else None,
        dead_jobs=counts.get("dead", 0),
        dead_kinds=dead_kinds,
        event_deliveries_dead=int(deliveries_dead or 0),
    )


def read_backup_status(path: str | None = None, now: datetime | None = None) -> BackupStatus:
    """The backup container writes this file (backup.sh); a missing or unreadable file is reported, not hidden."""
    file = Path(path or get_settings().backup_status_file)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(data["last_success"].replace("Z", "+00:00")) if data.get("last_success") else None
    except (OSError, ValueError, KeyError, TypeError):
        return BackupStatus(
            available=False, result=None, last_success=None, age_seconds=None, snapshot_id=None, error=None
        )
    age = int(((now or clock.now()) - last).total_seconds()) if last else None
    return BackupStatus(
        available=True, result=data.get("result"), last_success=last, age_seconds=age,
        snapshot_id=data.get("snapshot_id"), error=data.get("error"),
    )  # fmt: skip


def system_status(db: Session, ctx: Context) -> SystemStatus:
    ctx.require(P.SYSTEM_READ)
    counts = notifications.outbox_status_counts(db, ctx)
    settings = get_settings()
    smtp = parse_smtp_url(settings.smtp_url, settings.smtp_from)
    return SystemStatus(
        data_profile=settings.data_profile,
        regulated_host_baa=settings.regulated_host_baa if is_regulated() else None,
        regulated_backup_baa=settings.regulated_backup_baa if is_regulated() else None,
        version=settings.app_version,
        environment=settings.app_env,
        checked_at=clock.now(),
        queue=_queue(db, ctx),
        outbox=OutboxStatus(counts=counts, needs_attention=counts.get("failed", 0) + counts.get("unknown", 0)),
        backup=read_backup_status(),
        email=EmailStatus(configured=smtp is not None, host=smtp.host if smtp else None),
    )


def enqueue_test_email(db: Session, ctx: Context, recipient: str) -> uuid.UUID:
    ctx.require(P.SYSTEM_READ)  # this permission belongs only to owners/admins
    if not get_settings().smtp_url:
        raise Invalid("admin_ops.email_not_configured", detail="Configure SMTP before sending a test e-mail")
    message_id = notifications.enqueue_message(
        db,
        ctx,
        channel="email",  # deliberately not notifications.email_channel(): a simulated test proves nothing
        recipient=recipient,
        template_key="test_message",
        variables={"name": "from your shop — SMTP test"},
        idempotency_key=f"test-email:{ctx.business_id}:{new_id()}",
    )
    audit.record(
        db,
        ctx,
        action="admin_ops.test_email.queued",
        target_type="outbox_message",
        target_id=message_id,
        summary="Test e-mail queued",  # recipients stay in outbox, never audit summaries or logs
    )
    return message_id


# ------------------------------------------------------------------------------------------------ data lifecycle
EXPORT_FORMAT_VERSION = 1


def build_export(db: Session, ctx: Context) -> tuple[bytes, dict[str, Any]]:
    """A ZIP with one JSON file per table, grouped by module, plus a manifest (one business only)."""
    ctx.require(P.DATA_MANAGE)
    registry = get_registry()
    buffer = io.BytesIO()
    manifest: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": clock.now().isoformat(),
        "business_id": str(ctx.business_id),
        "modules": {},
    }
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for module, handler in registry.export_handlers.items():
            tables = handler(db, ctx)
            manifest["modules"][module] = {table: len(rows) for table, rows in tables.items()}
            for table, rows in tables.items():
                archive.writestr(
                    f"{module}/{table}.json", json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True)
                )
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    audit.record(
        db,
        ctx,
        action="admin_ops.export.created",
        target_type="business",
        target_id=ctx.business_id,
        summary="Full data export downloaded",
        changes={"tables": sum(len(t) for t in manifest["modules"].values())},
    )
    return buffer.getvalue(), manifest


def erase_customer(db: Session, ctx: Context, customer_id: uuid.UUID, confirm_name: str) -> ErasureRequest:
    """Erase one customer's personal data in every module, highest layer first, in one transaction."""
    ctx.require(P.DATA_MANAGE)
    assert ctx.business_id is not None
    target = crm.get_erasure_target(db, ctx, customer_id)  # locks the customer until the transaction ends
    if not target.display_name:  # already erased: report the earlier result, change nothing
        previous = db.scalar(
            select(ErasureRequest)
            .where(
                ErasureRequest.business_id == ctx.business_id,
                ErasureRequest.customer_id == customer_id,
                ErasureRequest.status.in_(("completed", "restricted")),
            )
            .order_by(ErasureRequest.created_at.desc())
        )
        if previous is not None:
            return previous
        raise Conflict("admin_ops.already_erased")
    if confirm_name.strip() != target.display_name:
        raise Invalid("admin_ops.confirmation_mismatch", detail="Type the customer's name exactly as shown to confirm")
    registry = get_registry()
    target = _with_links(db, ctx, target)
    reasons = [reason for check in registry.erasure_checks.values() for reason in check(db, ctx, target)]
    if reasons:
        raise Conflict("admin_ops.erasure_blocked", reasons=reasons)
    if is_regulated():
        return _restrict_customer(db, ctx, target)
    report: dict[str, dict[str, int]] = {}
    for module, handler in reversed(list(registry.erasure_handlers.items())):
        report[module] = handler(db, ctx, target)
    report["platform"] = _erase_platform(db, ctx, target)
    request = ErasureRequest(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=customer_id,
        requested_by=ctx.user_id,
        status="completed",
        report=report,
        completed_at=clock.now(),
    )
    db.add(request)
    db.flush()
    audit.record(
        db,
        ctx,
        action="admin_ops.erasure.completed",
        target_type="customer",
        target_id=customer_id,
        summary="Customer data erased",
        changes={"modules": {m: sum(c.values()) for m, c in report.items()}},
    )
    return request


def _with_links(db: Session, ctx: Context, target: ErasureTarget) -> ErasureTarget:
    """Ids of every record that belongs to the person. Repeated until nothing new is found, because one module's
    records lead to another's (an appointment id appears in a proposal's payload)."""
    hooks = get_registry().erasure_links.values()
    for _ in range(4):
        found = {i for hook in hooks for i in hook(db, ctx, target)} - target.linked_ids - {target.customer_id}
        if not found:
            break
        target = replace(target, linked_ids=target.linked_ids | found)
    return target


def _erase_platform(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Kernel tables that copy module data: event and job payloads, and cached responses of retried requests."""
    needles = target.needles
    ids = [str(i) for i in target.all_ids]
    events = db.execute(
        update(DomainEvent)
        .where(
            DomainEvent.business_id == ctx.business_id,
            DomainEvent.payload.is_distinct_from(func.redact_jsonb(DomainEvent.payload, needles)),
        )
        .values(payload=func.redact_jsonb(DomainEvent.payload, needles))
    )
    jobs = db.execute(
        update(Job)
        .where(
            Job.business_id == ctx.business_id, Job.payload.is_distinct_from(func.redact_jsonb(Job.payload, needles))
        )
        .values(payload=func.redact_jsonb(Job.payload, needles))
    )
    body = cast(IdempotencyKey.response_body, Text)
    cached = db.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.business_id == ctx.business_id,
            or_(*(body.contains(v) for v in (*needles, *ids))),
        )
    )
    return {
        "event_payloads_redacted": rows_affected(events),
        "job_payloads_redacted": rows_affected(jobs),
        "cached_responses_deleted": rows_affected(cached),
    }


def export_own(db: Session, ctx: Context) -> dict[str, list[dict[str, Any]]]:
    """admin_ops and the kernel's business-owned rows. Queue tables have no row-level security: filter explicitly."""
    tables = dump_models(db, ctx, [ErasureRequest, Job, DomainEvent])
    tables["event_deliveries"] = [
        {"event_id": str(d.event_id), "subscriber": d.subscriber, "status": d.status, "attempts": d.attempts,
         "done_at": d.done_at.isoformat() if d.done_at else None}
        for d in db.scalars(
            select(EventDelivery)
            .join(DomainEvent, DomainEvent.id == EventDelivery.event_id)
            .where(DomainEvent.business_id == ctx.business_id)
        )
    ]  # fmt: skip
    return tables


def list_erasures(db: Session, ctx: Context) -> list[ErasureRequest]:
    ctx.require(P.DATA_MANAGE)
    return list(
        db.scalars(
            select(ErasureRequest)
            .where(ErasureRequest.business_id == ctx.business_id)
            .order_by(ErasureRequest.created_at.desc())
            .limit(200)
        )
    )


def _restrict_customer(db: Session, ctx: Context, target: ErasureTarget) -> ErasureRequest:
    """Stop use/contact immediately, preserving records under a retention hold. No erasure hooks run."""
    report = {
        module: handler(db, ctx, target)
        for module, handler in reversed(list(get_registry().restriction_handlers.items()))
    }
    cached = db.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.business_id == ctx.business_id,
            cast(IdempotencyKey.response_body, Text).contains(str(target.customer_id)),
        )
    )
    report["platform"] = {"cached_responses_deleted": rows_affected(cached)}
    request = ErasureRequest(
        id=new_id(),
        business_id=ctx.business_id,
        customer_id=target.customer_id,
        requested_by=ctx.user_id,
        status="restricted",
        report=report,
        completed_at=clock.now(),
    )
    db.add(request)
    db.flush()
    audit.record(
        db,
        ctx,
        action="admin_ops.erasure.restricted",
        target_type="customer",
        target_id=target.customer_id,
        summary="Customer restricted; records retained",
    )
    return request
