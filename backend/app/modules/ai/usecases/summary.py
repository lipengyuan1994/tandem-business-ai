"""U3: summarize the last 30 messages and propose a reply through the shared tool path."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ForeignKeyConstraint, Index, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.modules.conversations import public as conversations
from app.platform.canonical import sha256_hex
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, DomainError, Invalid
from app.platform.models import BusinessScopedBase, scoped_table_args

from ..agent.contexts import staff_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router
from ..models import AiRun
from ..schemas import ThreadSummary
from ..structured import complete
from ..tools.base import RunState
from ..tools.common import cite, redact_contacts
from ..tools.registry import execute_tool, get_tool


class Operation(BusinessScopedBase):
    """Metadata only. Validated text stays in ai_runs/assistant_messages and their existing erasure paths."""

    __tablename__ = "ai_operations"
    user_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID]
    operation_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[uuid.UUID | None]
    proposal_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    __table_args__ = scoped_table_args(
        ForeignKeyConstraint(["business_id", "run_id"], ["ai_runs.business_id", "ai_runs.id"]),
        ForeignKeyConstraint(
            ["business_id", "message_id"], ["assistant_messages.business_id", "assistant_messages.id"]
        ),
        Index("uq_ai_operations_key", "business_id", "user_id", "kind", "resource_id", "operation_key", unique=True),
        Index(
            "uq_ai_operations_active_thread",
            "business_id",
            "resource_id",
            unique=True,
            postgresql_where=text("kind='copilot' and status='active'"),
        ),
    )


def claim_operation(db: Session, ctx: Context, kind: str, resource_id: uuid.UUID, key: str, body: Any) -> Operation:
    """A short transaction claims an operation; never retain a transaction over inference."""
    if not 1 <= len(key) <= 64:
        raise Invalid("ai.invalid_idempotency_key")
    digest = sha256_hex({"body": body, "permissions": sorted(ctx.permissions)})
    db.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"ai-operation:{ctx.business_id}:{ctx.user_id}:{kind}:{resource_id}:{key}"},
    )
    row = db.scalar(
        select(Operation)
        .where(
            Operation.business_id == ctx.business_id,
            Operation.user_id == ctx.user_id,
            Operation.kind == kind,
            Operation.resource_id == resource_id,
            Operation.operation_key == key,
        )
        .with_for_update()
    )
    if row is not None:
        if row.request_hash != digest:
            raise Conflict("ai.idempotency_key_reused")
        return row
    row = Operation(
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        kind=kind,
        resource_id=resource_id,
        operation_key=key,
        request_hash=digest,
        status="idle",
    )
    db.add(row)
    db.flush()
    return row


class SummaryOut(BaseModel):
    run_id: uuid.UUID
    summary: ThreadSummary
    proposal_id: uuid.UUID


def run(
    ctx: Context, conversation_id: uuid.UUID, *, router: Router | None = None, idempotency_key: str | None = None
) -> SummaryOut:
    ctx.require("ai.assistant.use")
    ctx.require("conversations.thread.read")
    operation_id = None
    if idempotency_key is not None:
        with transaction(ctx) as db:
            # Check resource visibility even when returning an existing result.
            conversations.get_thread(db, ctx, conversation_id, last_n=1)
            op: Operation | None = claim_operation(db, ctx, "summary", conversation_id, idempotency_key, {})
            assert op is not None
            if op.status == "succeeded":
                recorded = db.get(AiRun, op.run_id)
                assert recorded is not None and op.proposal_id is not None
                return SummaryOut(
                    run_id=recorded.id,
                    summary=ThreadSummary.model_validate(recorded.result),
                    proposal_id=op.proposal_id,
                )
            if op.status == "active":
                raise Conflict("ai.operation_in_progress", operation_id=str(op.id))
            op.status = "active"
            operation_id = op.id
    try:
        result = _run(ctx, conversation_id, router=router, operation_id=operation_id)
        if operation_id is not None:
            with transaction(ctx) as db:
                op = db.get(Operation, operation_id)
                assert op is not None
                op.status, op.run_id, op.proposal_id = "succeeded", result.run_id, result.proposal_id
        return result
    finally:
        if operation_id is not None:
            with transaction(ctx) as db:
                op = db.get(Operation, operation_id)
                if op is not None and op.status == "active":
                    op.status = "idle"


def _run(
    ctx: Context, conversation_id: uuid.UUID, *, router: Router | None, operation_id: uuid.UUID | None
) -> SummaryOut:
    ctx.require("ai.assistant.use")
    ctx.require("conversations.thread.read")
    assert ctx.business_id is not None and ctx.user_id is not None
    factory = staff_ctx_factory(ctx.business_id, ctx.user_id, ctx.permissions, "summary")
    with transaction(ctx) as db:
        thread = conversations.get_thread(db, ctx, conversation_id, last_n=30)
    data = {
        "channel": thread.channel,
        "status": thread.status,
        "visitor_name": redact_contacts(thread.visitor_name),
        "messages": [
            {"author_kind": m.author_kind, "direction": m.direction, "body": redact_contacts(m.body),
             "created_at": m.created_at.isoformat()}
            for m in thread.messages
        ],
    }  # fmt: skip

    def valid_reply(value: ThreadSummary) -> None:
        # The same constraints as the eventual tool call, so a bad draft gets the one structured repair.
        get_tool("propose_reply").args_model.model_validate(
            {"conversation_id": conversation_id, "text": value.suggested_reply}
        )

    trigger_key = f"summary:{conversation_id}:{operation_id or uuid.uuid4()}"
    with transaction(ctx) as db:
        recorded = db.scalar(
            select(AiRun)
            .where(
                AiRun.business_id == ctx.business_id,
                AiRun.trigger_key == trigger_key,
                AiRun.status == "succeeded",
            )
            .order_by(AiRun.started_at.desc())
            .limit(1)
        )
        cached = (recorded.id, ThreadSummary.model_validate(recorded.result)) if recorded is not None else None
    if cached is not None:
        run_id, value = cached
    else:
        run_id, value = complete(
            factory, use_case="summary", prompt=load_prompt("summary"),
            facts=[ContextBlock(label="conversation", source_id=conversation_id,
                                text=json.dumps(data, ensure_ascii=False), untrusted=True)],
            output_model=ThreadSummary, trigger_type="api", trigger_key=trigger_key, router=router,
            validate=valid_reply,
        )  # fmt: skip
    state = RunState(run_id=run_id, trigger_key=trigger_key, read_conversation_ids={conversation_id})
    cite(state, "get_conversation", conversation_id, data)
    observation = execute_tool(
        factory(run_id), "propose_reply", {"conversation_id": str(conversation_id), "text": value.suggested_reply},
        ["propose_reply"], state,
    )  # fmt: skip
    if not observation["ok"]:
        raise DomainError(observation["error"], observation.get("detail"))
    return SummaryOut(run_id=run_id, summary=value, proposal_id=uuid.UUID(str(observation["data"]["proposal_id"])))
