"""AI handlers run without a worker-held transaction."""

from __future__ import annotations

import uuid
from typing import Any

from app.platform.context import Context

from .usecases import intake


def intake_job(ctx: Context, payload: dict[str, Any]) -> None:
    if ctx.business_id is None:
        raise ValueError("ai.intake requires a business")
    intake.run(uuid.UUID(str(payload["message_id"])), ctx.business_id)


def assistant_retention_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases.copilot import retain_messages

    retain_messages(ctx)


def reorder_note_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import reorder_note

    if ctx.business_id is None:
        raise ValueError("ai.reorder_note requires a business")
    reorder_note.run(uuid.UUID(str(payload["item_id"])), ctx.business_id)


def procurement_request_quotes_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import procurement

    if ctx.business_id is None:
        raise ValueError("ai.procurement_request_quotes requires a business")
    procurement.request_quotes(uuid.UUID(str(payload["item_id"])), ctx.business_id)


def procurement_compare_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import procurement

    if ctx.business_id is None:
        raise ValueError("ai.procurement_compare requires a business")
    procurement.compare(uuid.UUID(str(payload["quote_request_id"])), ctx.business_id)


def procurement_scan_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import procurement

    if ctx.business_id is None:
        raise ValueError("ai.procurement_scan requires a business")
    procurement.scan(ctx.business_id)


def payment_followup_scan_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import payment_followup

    if ctx.business_id is None:
        raise ValueError("ai.payment_followup_scan requires a business")
    payment_followup.run(ctx.business_id)
