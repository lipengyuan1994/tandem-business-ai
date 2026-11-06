"""Transient dictation pipeline. Recording contains metadata only; no transcript/dictionary/result text."""

import json
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, Invalid

from . import recording, usage
from .agent.fencing import fence
from .agent.prompts import load_prompt
from .agent.protocol import ContextBlock
from .dictation_guard import clean, guard
from .errors import AiError
from .gateway.router import PolicyBlocked, Router, effective_policy, load_router
from .gateway.types import ChatMessage, ChatRequest, ChatResult, ProviderError, TranscriptionRequest
from .model_calls import budget_scope, policy_scope
from .schemas import Policy


class DictationPolish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=20000)


class DictationOut(BaseModel):
    text: str
    raw_text: str
    duration_s: float
    polished: bool
    warnings: list[str]


def available(ctx: Context, router: Router | None = None) -> Router:
    ctx.require("ai.assistant.use")
    router = router or load_router()
    with transaction(ctx) as db:
        policy = effective_policy(db, ctx)
        enabled = usage.effective_budget(db, ctx) > 0
    if router is None or not router.allowed("stt", policy) or not enabled:
        reason = "ai.off" if not enabled else f"{policy}: no allowed stt route"
        raise Conflict("voice.unavailable", reason=reason)
    return router


def _policy(ctx: Context) -> Policy:
    with transaction(ctx) as db:
        if usage.effective_budget(db, ctx) <= 0:
            raise Conflict("voice.unavailable", reason="ai.off")
        return effective_policy(db, ctx)


def polish(ctx: Context, raw: str, terms: list[str], router: Router, step_no: int = 2) -> tuple[str, bool, list[str]]:
    """Best effort only: callers always retain raw speech when this optional pass fails."""
    raw = clean(raw)
    prompt = load_prompt("dictation_polish", 1)
    req = ChatRequest(
        messages=[
            ChatMessage(role="system", content=prompt.text),
            ChatMessage(
                role="user",
                content=fence(
                    ContextBlock(
                        label="dictation",
                        text=json.dumps({"raw_text": raw, "dictionary": terms}, ensure_ascii=False),
                        untrusted=True,
                    )
                ),
            ),
        ],
        json_schema=DictationPolish.model_json_schema(),
        use_case="dictation_polish",
        max_output_tokens=router.limits.max_output_tokens_per_step,
        temperature=0,
        timeout_s=min(15.0, float(router.limits.step_timeout_s)),
    )
    # Refuse oversize context rather than trim away speech or dictionary and accidentally change meaning.
    if sum(len(m.content.encode()) for m in req.messages) > router.limits.max_input_tokens_per_step * 4:
        return raw, False, ["voice.polish_input_too_large"]
    try:
        with budget_scope(ctx), policy_scope(lambda: _policy(ctx)):
            result, _ = router.complete("writer", _policy(ctx), req)
        if ctx.ai_run_id is not None:
            recording.record_step(
                ctx,
                ctx.ai_run_id,
                recording.StepRecord(
                    step_no=step_no,
                    kind="completion",
                    provider=result.provider,
                    model=result.model,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    latency_ms=result.latency_ms,
                ),
            )
        text = DictationPolish.model_validate_json(result.text).text.strip()
        warning = guard(raw, text, terms)
        if warning:
            return raw, False, [warning]
        return text, True, []
    except Exception as exc:
        # Deliberately no exception text in logs or recordings (provider output may be present).
        code = getattr(exc, "code", None)
        warning = "voice.polish_budget_exhausted" if code == "ai.budget_exceeded" else "voice.polish_failed"
        return raw, False, [warning]


def transcribe(
    ctx: Context,
    audio: bytes,
    filename: str,
    mime: str,
    terms: list[str],
    operation_id: uuid.UUID,
    language: str | None = None,
) -> tuple[DictationOut, ChatResult]:
    router = available(ctx)
    run_id = uuid.uuid4()
    # Context is a frozen dataclass; preserve the staff's exact scope without introducing tool permissions.
    from dataclasses import replace

    run_ctx = replace(ctx, ai_run_id=run_id)
    recording.open_run(
        run_ctx,
        recording.RunMeta(
            use_case="dictation",
            prompt=load_prompt("dictation_polish", 1),
            trigger_type="api",
            trigger_key=f"dictation:{operation_id}",
            on_behalf_of_user_id=ctx.user_id,
        ),
    )
    try:
        req = TranscriptionRequest(
            audio=audio, filename=filename, mime=mime, language=language, timeout_s=float(router.limits.step_timeout_s)
        )
        with budget_scope(run_ctx), policy_scope(lambda: _policy(ctx)):
            result, _ = router.transcribe(_policy(ctx), req)
        seconds = result.usage.audio_seconds
        if seconds is None:
            raise ProviderError("server_error", "duration missing")
        recording.record_step(
            run_ctx,
            run_id,
            recording.StepRecord(
                step_no=1, kind="completion", provider=result.provider, model=result.model, latency_ms=result.latency_ms
            ),
        )
        if seconds > 125:
            raise Invalid("voice.duration_exceeded", duration_s=seconds, provider=result.provider, model=result.model)
        text, polished, warnings = polish(run_ctx, result.text, terms, router)
        recording.close_run(run_ctx, run_id, "succeeded", result={"polished": polished})
        return DictationOut(
            text=text, raw_text=clean(result.text), duration_s=seconds, polished=polished, warnings=warnings
        ), result
    except Exception as exc:
        code = getattr(exc, "code", "ai.provider_unavailable")
        recording.close_run(run_ctx, run_id, "failed", error_code=code)
        if isinstance(exc, PolicyBlocked):
            raise Conflict("voice.unavailable", reason="policy changed") from None
        if isinstance(exc, ProviderError):
            raise AiError("ai.provider_unavailable") from None
        raise
