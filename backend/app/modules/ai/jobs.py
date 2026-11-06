"""AI handlers run without a worker-held transaction."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from app.platform.context import Context, workflow_scope
from app.platform.db import transaction

from .errors import AiError
from .team_settings import enabled
from .usecases import intake


def guarded(handler: Callable[[Context, dict[str, Any]], None], key: str) -> Callable[[Context, dict[str, Any]], None]:
    def run(ctx: Context, payload: dict[str, Any]) -> None:
        with transaction(ctx) as db:
            if not enabled(db, ctx, key):
                return
        try:
            with workflow_scope(ctx):
                handler(ctx, payload)
        except AiError as exc:
            if not (exc.code.startswith("ai.chain_") or exc.code in {"ai.agent_disabled", "ai.agent_limit_reached"}):
                raise
            # Admission/provider paths record the exception. These controls are terminal, not retryable.

    return run


def intake_job(ctx: Context, payload: dict[str, Any]) -> None:
    if ctx.business_id is None:
        raise ValueError("ai.intake requires a business")
    intake.run(uuid.UUID(str(payload["message_id"])), ctx.business_id)


def assistant_retention_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases.copilot import retain_messages

    retain_messages(ctx)
    from app.platform.db import transaction
    from app.platform.registry import get_registry

    with transaction(ctx) as db:
        for handler in get_registry().retention_handlers.values():
            handler(db, ctx, payload)


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


def procurement_followup_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import procurement

    if ctx.business_id is None:
        raise ValueError("ai.procurement_followup requires a business")
    procurement.followup(
        ctx.business_id,
        purchase_order_id=uuid.UUID(str(payload["purchase_order_id"])) if payload.get("purchase_order_id") else None,
        quote_request_id=uuid.UUID(str(payload["quote_request_id"])) if payload.get("quote_request_id") else None,
        supplier_id=uuid.UUID(str(payload["supplier_id"])) if payload.get("supplier_id") else None,
    )


def payment_followup_scan_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import payment_followup

    if ctx.business_id is None:
        raise ValueError("ai.payment_followup_scan requires a business")
    payment_followup.scan(ctx.business_id)


def payment_followup_job(ctx: Context, payload: dict[str, Any]) -> None:
    from .usecases import payment_followup

    if ctx.business_id is None:
        raise ValueError("ai.payment_followup requires a business")
    payment_followup.run_invoice(uuid.UUID(str(payload["invoice_id"])), ctx.business_id)
