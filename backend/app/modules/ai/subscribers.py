"""Transactional event subscribers only enqueue work; no model calls here."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.conversations import public as conversations
from app.modules.purchasing import public as purchasing
from app.platform import clock, jobs
from app.platform.context import Context, system_context
from app.platform.errors import NotFound

from .agent.contexts import background_ctx_factory
from .public_intake import admit


def intake_trigger(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    if payload.get("author_kind") not in {"visitor", "customer"}:
        return
    message_id = uuid.UUID(str(payload["message_id"]))
    read_ctx = system_context(ctx.business_id, frozenset({"conversations.thread.read"}))
    try:
        source = conversations.get_message_context(db, read_ctx, message_id)
    except NotFound:
        pass  # Preserve stale-event enqueue behavior; a real queued message is checked again by intake.
    else:
        if source.ai_mode != "auto" or source.ai_generation != source.message_ai_generation:
            return
    if not admit(db, ctx, message_id):
        return
    jobs.enqueue(
        db, "ai.intake", {"message_id": str(message_id)}, business_id=ctx.business_id,
        dedupe_key=f"intake:{message_id}",
    )  # fmt: skip


def compare_trigger(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    if payload.get("all_answered") is not True:
        return
    request_id = uuid.UUID(str(payload["quote_request_id"]))
    jobs.enqueue(
        db,
        "ai.procurement_compare",
        {"quote_request_id": str(request_id)},
        business_id=ctx.business_id,
        dedupe_key=f"procurement_compare:{request_id}",
    )


def reorder_trigger(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    item_id = uuid.UUID(str(payload["item_id"]))
    day = clock.now().astimezone(UTC).date().isoformat()
    jobs.enqueue(
        db, "ai.reorder_note", {"item_id": str(item_id)}, business_id=ctx.business_id,
        dedupe_key=f"reorder_note:{item_id}:{day}",
    )  # fmt: skip

    assert ctx.business_id is not None
    read_ctx = background_ctx_factory(ctx.business_id)(uuid.uuid4())
    try:
        suppliers = purchasing.list_suppliers_for_item(db, read_ctx, item_id)
    except NotFound:
        # A stale inventory event still retains its reorder job; it cannot request quotes for a deleted item.
        return
    if len(suppliers) >= 2:
        jobs.enqueue(
            db,
            "ai.procurement_request_quotes",
            {"item_id": str(item_id)},
            business_id=ctx.business_id,
            dedupe_key=f"procurement_request_quotes:{item_id}:{day}",
        )


def handoff_trigger(db: Session, ctx: Context, payload: dict[str, Any], event_id: uuid.UUID) -> None:
    approvals.invalidate_ai_replies(
        db, ctx, uuid.UUID(str(payload["conversation_id"])), int(payload.get("ai_generation", 1))
    )
