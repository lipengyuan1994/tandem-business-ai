"""The agent loop (contracts §8 "Runner rules" and the status table; architecture §3, §7).

Bounded and recorded: each step checks the wall clock and the budget, calls the router outside any transaction,
records usage at once, validates the AgentStep (one repair), records the step, then finishes or runs the tool through
`execute_tool` (its own transaction) and feeds the fenced observation back. Never called inside a transaction.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import structlog
from pydantic import ValidationError

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict

from .. import effects, recording
from ..errors import AiError
from ..gateway.router import PolicyBlocked, Router, effective_policy, load_router
from ..gateway.routes import Limits, ModelClass
from ..gateway.types import ChatMessage, ChatRequest, ChatResult, ProviderError
from ..model_calls import budget_scope, policy_scope
from ..schemas import Policy, RunStatus
from ..tools.base import RunState
from ..tools.intake import validate_step, validation_summary
from ..tools.knowledge import resolve_citations
from ..tools.registry import authorized_tools, execute_tool, get_tool, tool_catalog
from .context import InputTooLarge, append_step, build_messages, trim_to_budget
from .contexts import CtxFactory
from .protocol import FinalStep, RunResult, RunSpec, StepEvent, ToolCallStep, agent_step_json_schema

log = structlog.get_logger(__name__)
_monotonic = time.monotonic  # tests replace it to simulate elapsed time

MAX_REPAIR_ERROR = 300


class StopRun(Exception):
    """Ends the loop with a recorded status."""

    def __init__(self, status: RunStatus, error_code: str | None) -> None:
        super().__init__(error_code or status)
        self.status = status
        self.error_code = error_code


def repair_message(exc: ValidationError | ValueError) -> str:
    """`Your last reply was not valid: <error, ≤ 300 chars>. Reply with exactly one valid JSON object.` Validation
    errors use codes and allowlisted static fields only: locations can contain arbitrary model-written keys."""
    if isinstance(exc, ValidationError):
        detail = validation_summary(exc)
    else:
        detail = str(exc)
    return f"Your last reply was not valid: {detail[:MAX_REPAIR_ERROR]}. Reply with exactly one valid JSON object."


def _registered(name: str) -> bool:
    try:
        get_tool(name)
    except KeyError:
        return False
    return True


def blocked_code(router: Router | None, model_class: ModelClass) -> str:
    """`ai.not_configured` when no route of this class has a usable provider at all, else `ai.disabled_by_policy`."""
    if router is None or not router.allowed(model_class, "hosted_any"):
        return "ai.not_configured"
    return "ai.disabled_by_policy"


def status_text(tool: str) -> str | None:
    """The tool's description up to the first period (shown to staff while the copilot works)."""
    try:
        description = get_tool(tool).description
    except KeyError:
        return None
    first = description.split(".", 1)[0].strip()
    return first or None


class ModelCaller:
    """Calls the router for one run: wall clock, budget, per-call timeout, usage recording, error mapping."""

    def __init__(self, ctx: Context, router: Router, model_class: ModelClass, policy: Policy, use_case: str) -> None:
        self.ctx = ctx
        self.router = router
        self.limits: Limits = router.limits
        self.model_class = model_class
        self.policy = policy
        self.use_case = use_case
        self.started = _monotonic()

    def remaining(self) -> float:
        return self.limits.run_timeout_s - (_monotonic() - self.started)

    def current_policy(self) -> Policy:
        with transaction(self.ctx) as db:
            self.policy = effective_policy(db, self.ctx)
        return self.policy

    def call(self, messages: list[ChatMessage], schema: dict[str, Any]) -> ChatResult:
        remaining = self.remaining()
        if remaining <= 0:
            raise StopRun("failed", "ai.run_timeout")
        try:
            messages[:] = trim_to_budget(messages, self.limits.max_input_tokens_per_step, schema)
        except InputTooLarge:
            raise StopRun("failed", "ai.input_too_large") from None
        remaining = self.remaining()
        if remaining <= 0:
            raise StopRun("failed", "ai.run_timeout")
        req = ChatRequest(
            messages=messages,
            json_schema=schema,
            max_output_tokens=min(800, self.limits.max_output_tokens_per_step)
            if self.model_class == "grounding"
            else self.limits.max_output_tokens_per_step,
            timeout_s=min(float(self.limits.step_timeout_s), remaining),
            use_case=self.use_case,
        )
        try:
            with budget_scope(self.ctx), policy_scope(self.current_policy):
                result, _entry = self.router.complete(self.model_class, self.policy, req)
        except AiError as exc:
            status: RunStatus = "budget_exceeded" if exc.code == "ai.budget_exceeded" else "failed"
            raise StopRun(status, exc.code) from None
        except PolicyBlocked:
            raise StopRun("policy_blocked", blocked_code(self.router, self.model_class)) from None
        except ProviderError as exc:
            if self.remaining() <= 0:
                raise StopRun("failed", "ai.run_timeout") from None
            log.warning("ai_run_provider_failed", run_id=str(self.ctx.ai_run_id), kind=exc.kind)
            raise StopRun("failed", "ai.provider_unavailable") from None
        if self.remaining() <= 0:
            raise StopRun("failed", "ai.run_timeout")
        return result


def _rebuild_state(ctx: Context, spec: RunSpec, run_id: uuid.UUID) -> RunState:
    """RunState from `ai_effects` of the same trigger: holds and proposals of a crashed attempt are recovered, so
    the tools replay them instead of creating them twice."""
    state = RunState(
        run_id=run_id,
        trigger_key=spec.trigger_key,
        trigger_message_id=spec.trigger_message_id,
        server_facts=dict(spec.server_facts),
    )
    if spec.use_case == "intake" and spec.trigger_message_id is not None:
        with transaction(ctx) as db:
            from app.modules.conversations import public as conversations

            message = conversations.get_message_context(db, ctx, spec.trigger_message_id)
            state.read_conversation_ids.add(message.conversation_id)
        from ..tools.common import cite

        for block in spec.context_blocks:
            if block.label in {"active_services", "opening_hours"} and not block.untrusted:
                state.db_facts[block.label] = json.loads(block.text)
            if block.label == "active_services" and not block.untrusted:
                for service in json.loads(block.text):
                    cite(state, "get_service_parts", uuid.UUID(service["service_id"]), service)
    with transaction(ctx) as db:
        for record in effects.effects_for_trigger(db, ctx, spec.trigger_key):
            if record.kind == "proposal":
                state.proposals[record.effect_key] = record.ref_id
            else:
                state.holds.setdefault(record.ref_id, {})
    return state


def run_agent(
    ctx_factory: CtxFactory,
    spec: RunSpec,
    *,
    router: Router | None = None,
    on_event: Callable[[StepEvent], None] | None = None,
) -> RunResult:
    run_id = uuid.uuid4()
    ctx = ctx_factory(run_id)
    router = router if router is not None else load_router()
    limits = router.limits if router is not None else Limits()
    emit = on_event or (lambda _event: None)
    model_class: ModelClass = spec.prompt.model_class
    meta = recording.RunMeta(
        use_case=spec.use_case, prompt=spec.prompt, trigger_type=spec.trigger_type, trigger_key=spec.trigger_key,
        on_behalf_of_user_id=ctx.on_behalf_of_user_id,
    )  # fmt: skip

    recording.close_stale_runs(ctx, spec.trigger_key, limits.run_timeout_s)
    recording.open_run(ctx, meta)
    try:
        state = _rebuild_state(ctx, spec, run_id)
        proposal_ids: list[uuid.UUID] = []
        proposal_titles: list[str] = []

        def finish(
            status: RunStatus,
            error_code: str | None = None,
            final: FinalStep | None = None,
            *,
            handoff_requested: bool = False,
        ) -> RunResult:
            needs_human = final.needs_human if final is not None else False
            result: dict[str, Any] = {"proposal_ids": [str(p) for p in proposal_ids]}
            if final is not None:
                result.update(text=final.message_to_staff, needs_human=final.needs_human)
            recording.close_run(ctx, run_id, status, error_code=error_code, needs_human=needs_human, result=result)
            if final is not None and status == "succeeded":
                emit(StepEvent(kind="final", run_id=run_id, text=final.message_to_staff))
            else:
                emit(StepEvent(kind="error", run_id=run_id, error_code=error_code))
            return RunResult(
                run_id=run_id, status=status, final=final, proposal_ids=list(proposal_ids), error_code=error_code,
                extra={"proposal_titles": list(proposal_titles),
                       **({"handoff_requested": True} if handoff_requested else {})},
            )  # fmt: skip

        if router is None:
            return finish("policy_blocked", "ai.not_configured")
        with transaction(ctx) as db:
            policy = effective_policy(db, ctx)
        if not router.allowed(model_class, policy):
            return finish("policy_blocked", blocked_code(router, model_class))

        caller = ModelCaller(ctx, router, model_class, policy, spec.use_case)
        tools = authorized_tools(ctx, spec.tools)
        messages = build_messages(spec.prompt, tool_catalog(tools), spec)
        schema = agent_step_json_schema()
        step_no = 0
        try:
            while step_no < limits.max_steps:
                result = caller.call(messages, schema)
                tokens = {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens}
                where = {"provider": result.provider, "model": result.model, "latency_ms": result.latency_ms}
                try:
                    step = validate_step(result.text, spec, proposal_ids, state)
                except ValueError as exc:
                    step_no += 1
                    recording.record_step(ctx, run_id, recording.StepRecord(
                        step_no=step_no, kind="repair", error_code="ai.invalid_model_output",
                        result_summary=validation_summary(exc), **tokens, **where,
                    ))  # fmt: skip
                    if step_no >= limits.max_steps:
                        raise StopRun("step_limit", "ai.step_limit") from None
                    retry = [
                        *messages,
                        ChatMessage(role="assistant", content=result.text),
                        ChatMessage(role="user", content=repair_message(exc)),
                    ]
                    result = caller.call(retry, schema)
                    tokens = {"input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens}
                    where = {"provider": result.provider, "model": result.model, "latency_ms": result.latency_ms}
                    try:
                        step = validate_step(result.text, spec, proposal_ids, state)
                    except ValueError as exc:
                        step_no += 1
                        recording.record_step(ctx, run_id, recording.StepRecord(
                            step_no=step_no, kind="repair", error_code="ai.invalid_model_output",
                            result_summary=validation_summary(exc), **tokens, **where,
                        ))  # fmt: skip
                        raise StopRun("failed", "ai.invalid_model_output") from None
                step_no += 1
                if caller.remaining() <= 0:
                    raise StopRun("failed", "ai.run_timeout")
                if isinstance(step, FinalStep):
                    recording.record_step(ctx, run_id, recording.StepRecord(step_no=step_no, kind="final", **tokens,
                                                                            **where))  # fmt: skip
                    if caller.remaining() <= 0:
                        raise StopRun("failed", "ai.run_timeout")
                    return finish("succeeded", None, step)
                observation = _run_tool(ctx, spec, step, state, emit, run_id, caller)
                recording.record_step(ctx, run_id, recording.StepRecord(
                    step_no=step_no, kind="tool_call", tool=step.tool[:100] if _registered(step.tool) else None,
                    # Rejected arguments can contain arbitrary free-text keys, including nested keys. Only a
                    # successful schema-validated call has server-defined keys safe for metadata recording.
                    arguments_redacted=recording.redact_arguments(step.arguments) if observation.get("ok") else {},
                    result_summary=recording.summarize_observation(observation),
                    error_code=None if observation.get("ok") else str(observation.get("error"))[:100],
                    **tokens, **where,
                ))  # fmt: skip
                _collect_proposal(observation, proposal_ids, proposal_titles, emit, run_id)
                if observation.get("ok") and observation.get("data", {}).get("handoff_requested"):
                    return finish(
                        "succeeded",
                        handoff_requested=True,
                        final=FinalStep(
                            kind="final",
                            message_to_staff="Handed to a person; AI replies are paused.",
                            proposal_ids=list(proposal_ids),
                            needs_human=True,
                        ),
                    )
                append_step(messages, step.model_dump_json(), observation)
            return finish("step_limit", "ai.step_limit")
        except StopRun as stop:
            return finish(stop.status, stop.error_code)
    except Exception:
        recording.close_run(ctx, run_id, "failed", error_code="ai.internal_error")
        log.warning("ai_run_failed_unexpectedly", run_id=str(run_id))
        raise


def _run_tool(
    ctx: Context,
    spec: RunSpec,
    step: ToolCallStep,
    state: RunState,
    emit: Callable[[StepEvent], None],
    run_id: uuid.UUID,
    caller: ModelCaller,
) -> dict[str, Any]:
    emit(StepEvent(kind="step", run_id=run_id, tool=step.tool, status_text=status_text(step.tool)))
    if caller.remaining() <= 0:
        raise StopRun("failed", "ai.run_timeout")
    if spec.use_case == "intake" and step.tool == "propose_reply" and step.tool in authorized_tools(ctx, spec.tools):
        from ..grounding import check
        from ..tools.proposals import ProposeReplyArgs

        try:
            args = ProposeReplyArgs.model_validate(step.arguments)
            citations = resolve_citations(args.citations, state)
        except (ValidationError, Conflict):
            pass  # execute_tool owns the normal invalid-argument observation
        else:
            if not args.knowledge_missing and not args.request_handoff:
                state.reply_grounding = check(
                    lambda run_id: replace(ctx, ai_run_id=run_id),
                    reply=args.text,
                    articles=[state.knowledge_citations[(c.article_id, c.version)] for c in citations],
                    db_facts=state.db_facts,
                    trigger_key=state.trigger_key,
                    router=caller.router,
                )
                state.checked_reply = args.text
                if caller.remaining() <= 0:
                    # Checking must not authorize an effect after its generating intake run has expired.
                    raise StopRun("failed", "ai.run_timeout")
    observation = execute_tool(ctx, step.tool, step.arguments, spec.tools, state)
    if observation.get("ok") and step.tool in {"list_services", "get_service_parts", "check_stock", "find_slots"}:
        state.db_facts.setdefault(step.tool, []).append(observation["data"])
    return observation


def _collect_proposal(
    observation: dict[str, Any],
    proposal_ids: list[uuid.UUID],
    titles: list[str],
    emit: Callable[[StepEvent], None],
    run_id: uuid.UUID,
) -> None:
    data = observation.get("data")
    if not observation.get("ok") or not isinstance(data, dict) or "proposal_id" not in data:
        return
    try:
        proposal_id = uuid.UUID(str(data["proposal_id"]))
    except ValueError:
        return
    if proposal_id in proposal_ids:
        return
    title = str(data.get("title") or "")[:200]
    proposal_ids.append(proposal_id)
    titles.append(title)
    emit(StepEvent(kind="proposal", run_id=run_id, proposal_id=proposal_id, proposal_title=title or None))


__all__ = ["blocked_code", "repair_message", "run_agent"]
