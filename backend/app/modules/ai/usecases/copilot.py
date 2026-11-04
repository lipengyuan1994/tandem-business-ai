"""U2: owned staff threads, short DB transactions, and a worker-to-SSE bridge (§14)."""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.platform import clock, ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import NotFound, Unauthenticated

from .. import recording
from ..agent.contexts import staff_ctx_factory, staff_permissions
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock, RunSpec, StepEvent
from ..agent.runner import StopRun, run_agent
from ..gateway.router import Router, load_router
from ..gateway.routes import ModelClass, RouteEntry
from ..gateway.types import ChatRequest, ChatResult
from ..models import AssistantMessage, AssistantThread
from ..schemas import Policy
from ..tools.registry import get_tool

log = structlog.get_logger(__name__)

# Every read tool, plus only the five U2 action tools. This is also the single source of staff-facing step text.
STATUS_TEXT = {
    "get_conversation": "Reading the conversation…",
    "find_customers": "Finding customers…",
    "get_customer": "Checking the customer…",
    "customer_history": "Checking customer history…",
    "list_services": "Checking services…",
    "get_service_parts": "Checking service parts…",
    "find_slots": "Checking free times…",
    "list_appointments": "Checking appointments…",
    "check_stock": "Checking stock…",
    "list_low_stock": "Checking low stock…",
    "list_suppliers_for_item": "Checking suppliers…",
    "get_quote_request": "Reading the quote request…",
    "compare_quotes": "Comparing supplier quotes…",
    "list_late_purchase_orders": "Checking late purchase orders…",
    "list_overdue_invoices": "Checking overdue invoices…",
    "today_summary": "Checking today's activity…",
    "propose_reply": "Drafting a reply proposal…",
    "propose_reorder": "Drafting a reorder proposal…",
    "propose_booking": "Drafting a booking proposal…",
    "hold_slot": "Holding the selected time…",
    "propose_customer": "Drafting a customer proposal…",
}


class ThreadCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=120)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class CopilotThreadOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadMessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    run_id: uuid.UUID | None
    proposal_ids: list[uuid.UUID]
    created_at: datetime


class CopilotThreadDetailOut(CopilotThreadOut):
    messages: list[ThreadMessageOut]


def _staff(ctx: Context) -> tuple[uuid.UUID, uuid.UUID]:
    ctx.require("ai.assistant.use")
    if ctx.business_id is None or ctx.user_id is None:
        raise Unauthenticated("auth.unauthenticated")
    return ctx.business_id, ctx.user_id


def _owned(db: Session, ctx: Context, thread_id: uuid.UUID) -> AssistantThread:
    business_id, user_id = _staff(ctx)
    thread = db.scalar(select(AssistantThread).where(
        AssistantThread.business_id == business_id, AssistantThread.user_id == user_id,
        AssistantThread.id == thread_id,
    ))  # fmt: skip
    if thread is None:
        raise NotFound("ai.thread_not_found")
    return thread


def list_threads(ctx: Context) -> list[CopilotThreadOut]:
    business_id, user_id = _staff(ctx)
    with transaction(ctx) as db:
        rows = db.scalars(select(AssistantThread).where(
            AssistantThread.business_id == business_id, AssistantThread.user_id == user_id,
        ).order_by(AssistantThread.created_at.desc(), AssistantThread.id.desc()).limit(50))  # fmt: skip
        return [CopilotThreadOut.model_validate(row, from_attributes=True) for row in rows]


def create_thread(ctx: Context, body: ThreadCreate) -> CopilotThreadOut:
    business_id, user_id = _staff(ctx)
    with transaction(ctx) as db:
        thread = AssistantThread(business_id=business_id, user_id=user_id, title=body.title)
        db.add(thread)
        db.flush()
        return CopilotThreadOut.model_validate(thread, from_attributes=True)


def get_thread(ctx: Context, thread_id: uuid.UUID) -> CopilotThreadDetailOut:
    with transaction(ctx) as db:
        thread = _owned(db, ctx, thread_id)
        rows = db.scalars(select(AssistantMessage).where(
            AssistantMessage.business_id == ctx.business_id, AssistantMessage.thread_id == thread_id,
        ).order_by(AssistantMessage.created_at, AssistantMessage.id))  # fmt: skip
        return CopilotThreadDetailOut(
            **CopilotThreadOut.model_validate(thread, from_attributes=True).model_dump(),
            messages=[ThreadMessageOut.model_validate(row, from_attributes=True) for row in rows],
        )


@dataclass(frozen=True)
class PreparedMessage:
    ctx: Context
    thread_id: uuid.UUID
    spec: RunSpec


def prepare_message(ctx: Context, thread_id: uuid.UUID, body: MessageCreate) -> PreparedMessage:
    """Ownership, validation and rate-limit errors happen before the HTTP stream starts."""
    _staff(ctx)
    prompt = load_prompt("copilot", 1)
    permissions = staff_permissions(ctx.permissions, "copilot")
    tools = [name for name in STATUS_TEXT if get_tool(name).permission in permissions]
    with transaction(ctx) as db:
        thread = _owned(db, ctx, thread_id)
        ratelimit.hit(db, f"ai:copilot:{ctx.user_id}", limit=30, window_seconds=3600)
        message = AssistantMessage(
            business_id=ctx.business_id, thread_id=thread_id, role="user", content=body.content,
            created_at=clock.now(),
        )  # fmt: skip
        db.add(message)
        thread.updated_at = clock.now()
        db.flush()
        recent = list(db.scalars(select(AssistantMessage).where(
            AssistantMessage.business_id == ctx.business_id, AssistantMessage.thread_id == thread_id,
        ).order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc()).limit(10)))  # fmt: skip
        blocks = [
            # Model replies can quote customer/supplier tool data. Replaying them as trusted history would
            # upgrade those instructions on the next staff turn. Only authenticated staff text is trusted.
            ContextBlock(label=f"{m.role} message", source_id=m.id, text=m.content, untrusted=m.role == "assistant")
            for m in reversed(recent)
        ]
        spec = RunSpec(
            use_case="copilot", prompt=prompt, tools=tools,
            task="Answer the latest staff message using the available tools and the thread history.",
            context_blocks=blocks, trigger_type="api", trigger_key=f"copilot:{message.id}",
        )  # fmt: skip
    return PreparedMessage(ctx=ctx, thread_id=thread_id, spec=spec)


class _DisconnectRouter(Router):
    """The runner's existing StopRun path closes the run at the next call boundary, after the current step."""

    def __init__(self, inner: Router, stopped: threading.Event) -> None:
        super().__init__(inner.routes, inner.adapters)
        self.inner = inner
        self.stopped = stopped

    def complete(self, model_class: ModelClass, policy: Policy, req: ChatRequest) -> tuple[ChatResult, RouteEntry]:
        if self.stopped.is_set():
            raise StopRun("failed", "ai.interrupted")
        return self.inner.complete(model_class, policy, req)


def run_message(prepared: PreparedMessage, emit: Callable[[StepEvent], None], stopped: threading.Event) -> None:
    """Worker only. Buffer the terminal event until the final message has committed."""
    business_id, user_id = _staff(prepared.ctx)
    factory = staff_ctx_factory(business_id, user_id, prepared.ctx.permissions, "copilot")
    run_ctx: Context | None = None
    terminal: StepEvent | None = None

    def context(run_id: uuid.UUID) -> Context:
        nonlocal run_ctx
        run_ctx = factory(run_id)
        return run_ctx

    def on_event(event: StepEvent) -> None:
        nonlocal terminal
        if event.kind in ("final", "error"):
            terminal = event
        else:
            if event.kind == "step":
                event = event.model_copy(update={"status_text": STATUS_TEXT.get(event.tool or "", "Checking…")})
            emit(event)

    try:
        router = load_router()
        result = run_agent(
            context, prepared.spec, router=_DisconnectRouter(router, stopped) if router is not None else None,
            on_event=on_event,
        )  # fmt: skip
        if result.final is not None and result.status == "succeeded":
            with transaction(prepared.ctx) as db:
                thread = _owned(db, prepared.ctx, prepared.thread_id)
                db.add(AssistantMessage(
                    business_id=business_id, thread_id=prepared.thread_id, role="assistant",
                    content=result.final.message_to_staff, run_id=result.run_id, proposal_ids=result.proposal_ids,
                    created_at=clock.now(),
                ))  # fmt: skip
                thread.updated_at = clock.now()
        if terminal is not None:
            emit(terminal)
    except Exception:
        # Never include staff/model text or exception details in logs or SSE.
        log.warning("ai_copilot_interrupted", run_id=str(run_ctx.ai_run_id) if run_ctx else None)
        if run_ctx is not None and run_ctx.ai_run_id is not None:
            recording.close_run(run_ctx, run_ctx.ai_run_id, "failed", error_code="ai.interrupted")
            emit(StepEvent(kind="error", run_id=run_ctx.ai_run_id, error_code="ai.interrupted"))
        else:
            raise


async def stream_message(prepared: PreparedMessage) -> AsyncGenerator[str]:
    """A cancelled/disconnected StreamingResponse sets the worker's stop flag without waiting on inference."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StepEvent | None] = asyncio.Queue()
    stopped = threading.Event()

    def put(event: StepEvent | None) -> None:
        if not stopped.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        try:
            run_message(prepared, put, stopped)
        finally:
            put(None)

    future = loop.run_in_executor(None, worker)
    try:
        while (event := await queue.get()) is not None:
            yield f"event: {event.kind}\ndata: {event.model_dump_json()}\n\n"
            if event.kind in ("final", "error"):
                break
        # Propagate only failures that happened before the worker could create a run/error event.
        if future.done():
            future.result()
    finally:
        stopped.set()


class CopilotResponse(StreamingResponse):
    """Close the generator explicitly even when disconnect/cancellation happens while ASGI is sending a chunk."""

    def __init__(self, prepared: PreparedMessage) -> None:
        self.events = stream_message(prepared)
        super().__init__(
            self.events, media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )  # fmt: skip

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.events.aclose()


def retain_messages(ctx: Context) -> None:
    """Per-business retention: strictly older than 90 days, then threads with no remaining messages."""
    if ctx.business_id is None:
        raise ValueError("ai.assistant_retention requires a business")
    with transaction(ctx) as db:
        db.execute(delete(AssistantMessage).where(
            AssistantMessage.business_id == ctx.business_id,
            AssistantMessage.created_at < clock.now() - timedelta(days=90),
        ))  # fmt: skip
        has_message = select(AssistantMessage.id).where(
            AssistantMessage.business_id == ctx.business_id, AssistantMessage.thread_id == AssistantThread.id,
        ).exists()  # fmt: skip
        db.execute(delete(AssistantThread).where(
            AssistantThread.business_id == ctx.business_id, ~has_message,
        ))  # fmt: skip
