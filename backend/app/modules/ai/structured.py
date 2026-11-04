"""Structured completion (contracts §9): one call → one validated object, with one repair, recorded as a run.

Used by the single-shot use cases (summary, reorder note, briefing, campaign draft, payment reminder, extraction).
Same limits, budget, policy and recording as the agent loop; no tools. Never called inside a transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.platform.db import transaction

from . import recording
from .agent.contexts import CtxFactory
from .agent.fencing import fence
from .agent.prompts import Prompt
from .agent.protocol import ContextBlock
from .agent.runner import ModelCaller, StopRun, blocked_code, repair_message
from .errors import AiError
from .gateway.router import Router, effective_policy, load_router
from .gateway.types import ChatMessage
from .schemas import UseCaseId

TriggerType = Literal["event", "job", "api", "cron"]


def _messages(prompt: Prompt, facts: list[ContextBlock]) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=prompt.text),
        ChatMessage(role="user", content="\n\n".join(fence(b) for b in facts) or "(no facts)"),
    ]


def complete[T: BaseModel](
    ctx_factory: CtxFactory,
    *,
    use_case: UseCaseId,
    prompt: Prompt,
    facts: list[ContextBlock],
    output_model: type[T],
    trigger_type: str,
    trigger_key: str,
    router: Router | None = None,
    validate: Callable[[T], None] | None = None,
) -> tuple[uuid.UUID, T]:
    """One call (+ one repair, also used when `validate` raises ValueError, e.g. invented numbers), recorded as a run
    with one 'completion' step. Raises AiError (§15) on budget/policy/provider/invalid output. Owns its transactions."""
    run_id = uuid.uuid4()
    ctx = ctx_factory(run_id)
    router = router if router is not None else load_router()
    meta = recording.RunMeta(
        use_case=use_case, prompt=prompt, trigger_type=trigger_type,
        trigger_key=trigger_key, on_behalf_of_user_id=ctx.on_behalf_of_user_id,
    )  # fmt: skip
    recording.open_run(ctx, meta)

    def fail(stop: StopRun) -> AiError:
        recording.close_run(ctx, run_id, stop.status, error_code=stop.error_code)
        return AiError(stop.error_code or "ai.provider_unavailable", run_id=str(run_id))

    if router is None:
        raise fail(StopRun("policy_blocked", "ai.not_configured"))
    with transaction(ctx) as db:
        policy = effective_policy(db, ctx)
    if not router.allowed(prompt.model_class, policy):
        raise fail(StopRun("policy_blocked", blocked_code(router, prompt.model_class)))

    caller = ModelCaller(ctx, router, prompt.model_class, policy, use_case)
    schema = output_model.model_json_schema()
    messages = _messages(prompt, facts)
    step_no = 0
    try:
        for attempt in (1, 2):
            if step_no >= router.limits.max_steps:
                raise StopRun("step_limit", "ai.step_limit")
            result = caller.call(messages, schema)
            step_no += 1
            record = recording.StepRecord(
                step_no=step_no, kind="completion", input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens, latency_ms=result.latency_ms, provider=result.provider,
                model=result.model,
            )  # fmt: skip
            try:
                value = output_model.model_validate_json(result.text)
                if validate is not None:
                    validate(value)
            except (ValidationError, ValueError) as exc:  # ValidationError is a ValueError; validate raises ValueError
                recording.record_step(
                    ctx, run_id, record.model_copy(update={"kind": "repair", "error_code": "ai.invalid_model_output"})
                )
                if attempt == 2:
                    raise StopRun("failed", "ai.invalid_model_output") from None
                messages = [
                    *messages,
                    ChatMessage(role="assistant", content=result.text),
                    ChatMessage(role="user", content=repair_message(exc)),
                ]
                continue
            recording.record_step(ctx, run_id, record)
            recording.close_run(ctx, run_id, "succeeded", result=value.model_dump(mode="json"))
            return run_id, value
    except StopRun as stop:
        raise fail(stop) from None
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["complete"]
