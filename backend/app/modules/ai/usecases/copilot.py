"""U2: owned staff threads, short DB transactions, and a worker-to-SSE bridge (§14)."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.platform import clock, ratelimit
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, RateLimited

from .. import recording
from ..agent.contexts import staff_ctx_factory, staff_permissions
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock, RunSpec, StepEvent
from ..agent.runner import StopRun, run_agent
from ..gateway.router import Router, load_router
from ..gateway.routes import ModelClass, RouteEntry
from ..gateway.types import ChatRequest, ChatResult
from ..models import AiRun, AssistantMessage
from ..schemas import Policy
from ..tools.registry import authorized_tools
from .copilot_cards import cards
from .copilot_cards import owned as _owned
from .copilot_threads import (
    CopilotThreadDetailOut as CopilotThreadDetailOut,
)
from .copilot_threads import (
    CopilotThreadOut as CopilotThreadOut,
)
from .copilot_threads import (
    MessageCreate as MessageCreate,
)
from .copilot_threads import (
    ThreadCreate as ThreadCreate,
)
from .copilot_threads import (
    ThreadMessageOut as ThreadMessageOut,
)
from .copilot_threads import (
    _staff,
)
from .copilot_threads import (
    create_thread as create_thread,
)
from .copilot_threads import (
    get_thread as get_thread,
)
from .copilot_threads import (
    list_threads as list_threads,
)
from .copilot_threads import (
    retain_messages as retain_messages,
)
from .summary import Operation, claim_operation

log = structlog.get_logger(__name__)
_GENERATION_SLOTS = threading.BoundedSemaphore(4)

# Every read tool, plus only the five U2 action tools. This is also the single source of staff-facing step text.
STATUS_TEXT = {
    "search_knowledge": "Searching published knowledge…",
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
    "propose_send_po": "Drafting the next purchase order step…",
    "propose_quote_request": "Drafting a supplier quote request…",
    "propose_supplier_quote": "Drafting a supplier quote proposal…",
    "propose_po_from_quote": "Drafting a purchase order proposal…",
    "propose_supplier_followup": "Drafting a supplier follow-up…",
}


def _next_message_time(db: Session, thread_id: uuid.UUID) -> datetime:
    latest = db.scalar(select(func.max(AssistantMessage.created_at)).where(AssistantMessage.thread_id == thread_id))
    return max(clock.now(), latest + timedelta(microseconds=1)) if latest is not None else clock.now()


@dataclass(frozen=True)
class PreparedMessage:
    ctx: Context
    thread_id: uuid.UUID
    spec: RunSpec
    operation_id: uuid.UUID
    replay: StepEvent | None = None
    capacity: threading.BoundedSemaphore | None = None
    released: threading.Event = field(default_factory=threading.Event)
    release_lock: threading.Lock = field(default_factory=threading.Lock)


def prepare_message(
    ctx: Context, thread_id: uuid.UUID, body: MessageCreate, idempotency_key: str | None = None
) -> PreparedMessage:
    """Ownership, validation and rate-limit errors happen before the HTTP stream starts."""
    business_id, user_id = _staff(ctx)
    prompt = load_prompt("copilot", 3)
    permissions = staff_permissions(ctx.permissions, "copilot")
    tools = authorized_tools(
        staff_ctx_factory(business_id, user_id, permissions, "copilot")(uuid.uuid4()), list(STATUS_TEXT)
    )
    acquired = False
    try:
        with transaction(ctx) as db:
            thread = _owned(db, ctx, thread_id, lock=True)
            op = claim_operation(db, ctx, "copilot", thread_id, idempotency_key or str(uuid.uuid4()), body.model_dump())
            replay = None
            if op.status in {"succeeded", "failed"}:
                run = db.get(AiRun, op.run_id) if op.run_id is not None else None
                if run is None:
                    raise Conflict(
                        "ai.operation_interrupted",
                        "This turn stopped before generation started.",
                        operation_id=str(op.id),
                    )
                else:
                    replay = StepEvent(
                        kind="final" if op.status == "succeeded" else "error",
                        run_id=run.id,
                        text=run.result.get("text") if op.status == "succeeded" else None,
                        error_code=run.error_code,
                    )
            elif (
                db.scalar(
                    select(Operation.id)
                    .where(
                        Operation.business_id == ctx.business_id,
                        Operation.kind == "copilot",
                        Operation.resource_id == thread_id,
                        Operation.status == "active",
                    )
                    .limit(1)
                )
                is not None
            ):
                raise Conflict(
                    "ai.thread_busy", "An answer is already being generated for this thread.", operation_id=str(op.id)
                )
            if replay is None:
                ratelimit.hit(db, f"ai:copilot:{ctx.user_id}", limit=30, window_seconds=3600)
                if not _GENERATION_SLOTS.acquire(blocking=False):
                    raise RateLimited("ai.copilot_busy", retry_after_seconds=1)
                acquired = True
                op.status = "active"
                message = AssistantMessage(
                    business_id=ctx.business_id,
                    thread_id=thread_id,
                    role="user",
                    content=body.content,
                    created_at=_next_message_time(db, thread_id),
                )
                db.add(message)
                thread.updated_at = clock.now()
                db.flush()
                op.message_id = message.id
            message_id, operation_id = op.message_id, op.id
            recent = list(db.scalars(select(AssistantMessage).where(
                AssistantMessage.business_id == ctx.business_id, AssistantMessage.thread_id == thread_id,
            ).order_by(AssistantMessage.created_at.desc(), AssistantMessage.id.desc()).limit(10)))  # fmt: skip
            blocks = [
                # Model replies can quote customer/supplier tool data. Replaying them as trusted history would
                # upgrade those instructions on the next staff turn. Only authenticated staff text is trusted.
                ContextBlock(label=f"{m.role} message", source_id=m.id, text=m.content, untrusted=m.role == "assistant")
                for m in reversed(recent)
            ]
            for message in reversed(recent):
                action_cards = cards(db, ctx, message)
                if action_cards:
                    blocks.append(
                        ContextBlock(
                            label="Current proposal status and result",
                            source_id=message.id,
                            text=json.dumps([card.model_dump(mode="json") for card in action_cards]),
                            untrusted=True,
                        )
                    )
            spec = RunSpec(
                use_case="copilot", prompt=prompt, tools=tools,
                task="Answer the latest staff message using the available tools and the thread history.",
                context_blocks=blocks, trigger_type="api", trigger_key=f"copilot:{message_id}",
            )  # fmt: skip
    except BaseException:
        if acquired:
            _GENERATION_SLOTS.release()
        raise
    return PreparedMessage(
        ctx=ctx,
        thread_id=thread_id,
        spec=spec,
        operation_id=operation_id,
        replay=replay,
        capacity=_GENERATION_SLOTS if acquired else None,
    )


class _DisconnectRouter(Router):
    """The runner's existing StopRun path closes the run at the next call boundary, after the current step."""

    def __init__(self, inner: Router, stopped: threading.Event) -> None:
        self.routes, self.adapters, self.limits = inner.routes, inner.adapters, inner.limits
        self.inner = inner
        self.stopped = stopped

    def allowed(self, model_class: ModelClass, policy: Policy) -> list[RouteEntry]:
        return self.inner.allowed(model_class, policy)

    def complete(self, model_class: ModelClass, policy: Policy, req: ChatRequest) -> tuple[ChatResult, RouteEntry]:
        if self.stopped.is_set():
            raise StopRun("failed", "ai.interrupted")
        return self.inner.complete(model_class, policy, req)


def run_message(prepared: PreparedMessage, emit: Callable[[StepEvent], None], stopped: threading.Event) -> None:
    try:
        _run_message(prepared, emit, stopped)
    finally:
        release_generation(prepared)


def release_generation(prepared: PreparedMessage) -> None:
    with prepared.release_lock:
        if prepared.released.is_set() or prepared.capacity is None:
            return
        prepared.released.set()
        try:
            with transaction(prepared.ctx) as db:
                op = db.scalar(select(Operation).where(Operation.id == prepared.operation_id).with_for_update())
                if op is not None and op.status == "active":
                    op.status = "failed"
        finally:
            prepared.capacity.release()


def _run_message(prepared: PreparedMessage, emit: Callable[[StepEvent], None], stopped: threading.Event) -> None:
    """Worker only. Buffer the terminal event until the final message has committed."""
    if stopped.is_set():
        return
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
                    created_at=_next_message_time(db, prepared.thread_id),
                ))  # fmt: skip
                thread.updated_at = clock.now()
                operation = db.get(Operation, prepared.operation_id)
                assert operation is not None
                operation.status, operation.run_id = "succeeded", result.run_id
        with transaction(prepared.ctx) as db:
            op = db.get(Operation, prepared.operation_id)
            assert op is not None
            op.status = "succeeded" if result.status == "succeeded" else "failed"
            op.run_id = result.run_id
        if terminal is not None:
            emit(terminal)
    except Exception:
        # Never include staff/model text or exception details in logs or SSE.
        log.warning("ai_copilot_interrupted", run_id=str(run_ctx.ai_run_id) if run_ctx else None)
        if run_ctx is not None and run_ctx.ai_run_id is not None:
            recording.close_run(run_ctx, run_ctx.ai_run_id, "failed", error_code="ai.interrupted")
            with transaction(prepared.ctx) as db:
                op = db.get(Operation, prepared.operation_id)
                assert op is not None
                op.run_id = run_ctx.ai_run_id
            emit(StepEvent(kind="error", run_id=run_ctx.ai_run_id, error_code="ai.interrupted"))
        else:
            raise


async def stream_message(prepared: PreparedMessage) -> AsyncGenerator[str]:
    """A cancelled/disconnected StreamingResponse sets the worker's stop flag without waiting on inference."""
    if prepared is not None and prepared.replay is not None:
        replay = prepared.replay
        yield f"event: {replay.kind}\ndata: {replay.model_dump_json()}\n\n"
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StepEvent | None] = asyncio.Queue()
    stopped = threading.Event()
    started = threading.Event()
    ownership = threading.Lock()

    def put(event: StepEvent | None) -> None:
        if not stopped.is_set():
            loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        with ownership:
            if stopped.is_set():
                return
            started.set()
        try:
            run_message(prepared, put, stopped)
        finally:
            if prepared is not None:
                release_generation(prepared)
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
        with ownership:
            stopped.set()
            future.cancel()
            if not started.is_set() and prepared is not None:
                release_generation(prepared)


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
