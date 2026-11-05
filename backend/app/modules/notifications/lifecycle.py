"""Notifications: export, and removal of an erased person's address and message variables (M18)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.platform import clock
from app.platform.context import Context
from app.platform.crud import rows_affected
from app.platform.export import ExportTables, export_tables, stream_models
from app.platform.registry import ErasureTarget

from .models import DeliveryAttempt, InAppNotification, OutboxMessage

# Capability links are credentials and are left out of the export.
SECRET_VARIABLES = frozenset({"unsubscribe_url", "unsubscribe_token", "quote_url"})


def _procurement_message() -> ColumnElement[bool]:
    return or_(
        and_(OutboxMessage.template_key == "quote_request", OutboxMessage.related_type == "quote_request"),
        and_(
            OutboxMessage.template_key == "supplier_followup",
            OutboxMessage.related_type.in_(("quote_request", "purchase_order")),
        ),
    )


def links(db: Session, ctx: Context, target: ErasureTarget) -> set[uuid.UUID]:
    """A followup may be the only copy mentioning the customer; link its purchasing record too."""
    body = OutboxMessage.variables["body"].astext
    return {
        row_id
        for row_id in db.scalars(
            select(OutboxMessage.related_id).where(
                OutboxMessage.business_id == ctx.business_id,
                _procurement_message(),
                OutboxMessage.related_id.is_not(None),
                body.is_distinct_from(func.redact_text(body, target.needles)),
            )
        )
        if row_id is not None
    }


def export(db: Session, ctx: Context, *, streaming: bool = False) -> ExportTables:
    tables = stream_models(db, ctx, [OutboxMessage, DeliveryAttempt, InAppNotification])
    rows = tables["outbox_messages"]

    def safe_rows() -> Iterator[dict[str, Any]]:
        for row in rows:
            if isinstance(row.get("variables"), dict):
                row["variables"] = {k: v for k, v in row["variables"].items() if k not in SECRET_VARIABLES}
            yield row

    tables["outbox_messages"] = safe_rows()
    return export_tables(tables, streaming=streaming)


def erase(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    """Messages to the person (by address, or sent from one of their conversations) keep their status and ids; the
    recipient and the message text are removed. In-app notifications lose the person's names and addresses."""
    addresses = {a.lower() for a in (*target.emails, *target.phones)}
    addressed = func.coalesce(func.lower(OutboxMessage.recipient).in_(addresses), False)
    procurement = and_(_procurement_message(), OutboxMessage.related_id.in_(target.all_ids))
    outbox = db.execute(
        update(OutboxMessage)
        .where(
            OutboxMessage.business_id == ctx.business_id,
            or_(addressed, and_(OutboxMessage.id.in_(target.all_ids), ~procurement)),
            or_(OutboxMessage.recipient.is_distinct_from("[erased]"), OutboxMessage.variables != {}),
        )
        .values(recipient=case((OutboxMessage.recipient.is_(None), None), else_="[erased]"), variables={})
    )
    # These are supplier contacts, not the erased customer's addresses. Scrub the copied text,
    # preserve capability variables byte-for-byte, and durably block future claims/retries.
    procurement_outbox = db.execute(
        update(OutboxMessage)
        .where(OutboxMessage.business_id == ctx.business_id, procurement, ~addressed)
        .values(
            variables=OutboxMessage.variables.op("||")({"body": "[erased]"}),
            restricted_at=func.coalesce(OutboxMessage.restricted_at, clock.now()),
            status=case(
                (OutboxMessage.status.in_(("pending", "unknown", "sending")), "failed"), else_=OutboxMessage.status
            ),
            last_error=case(
                (OutboxMessage.status.in_(("pending", "unknown", "sending")), "customer erased; delivery blocked"),
                else_=OutboxMessage.last_error,
            ),
        )
    )
    in_app = 0
    needles = target.needles
    linked = [str(i) for i in target.all_ids]
    for row in db.scalars(select(InAppNotification).where(InAppNotification.business_id == ctx.business_id)):
        if row.link and any(i in row.link for i in linked):
            title, body = "[erased]", ""
        else:
            title, body = _redact(row.title, needles), _redact(row.body, needles)
        if (title, body) != (row.title, row.body):
            row.title, row.body = title, body
            in_app += 1
    return {
        "outbox_messages_erased": rows_affected(outbox),
        "procurement_messages_redacted": rows_affected(procurement_outbox),
        "in_app_notifications_redacted": in_app,
    }


def _redact(text: str, needles: list[str]) -> str:
    for needle in needles:
        text = text.replace(needle, "[erased]")
    return text


def restrict(db: Session, ctx: Context, target: ErasureTarget) -> dict[str, int]:
    addresses = {a.lower() for a in (*target.emails, *target.phones)}
    result = db.execute(
        update(OutboxMessage)
        .where(
            OutboxMessage.business_id == ctx.business_id,
            or_(
                func.lower(OutboxMessage.recipient).in_(addresses),
                OutboxMessage.id.in_(target.all_ids),
                OutboxMessage.related_id.in_(target.all_ids),
            ),
        )
        .values(
            restricted_at=func.coalesce(OutboxMessage.restricted_at, clock.now()),
            status=case(
                (OutboxMessage.status.in_(("pending", "unknown", "sending")), "failed"), else_=OutboxMessage.status
            ),
            last_error=case(
                (OutboxMessage.status.in_(("pending", "unknown", "sending")), "customer restricted; delivery blocked"),
                else_=OutboxMessage.last_error,
            ),
        )
    )
    return {"deliveries_blocked": rows_affected(result)}
