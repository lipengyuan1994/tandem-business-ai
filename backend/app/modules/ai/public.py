"""Public API of the ai module. Other modules may import ONLY from this file."""

from __future__ import annotations

from typing import get_args

from sqlalchemy.orm import Session

from app.platform.context import Context

from . import usage
from .dictation import DictationOut
from .dictation import available as voice_available
from .dictation import transcribe as transcribe_dictation
from .dictation_dictionary import dictionary as dictation_dictionary
from .gateway.router import effective_policy, load_router
from .gateway.routes import ModelClass
from .schemas import AiStatus, ModelClassStatus


def status(db: Session, ctx: Context) -> AiStatus:
    """Whether AI features can run for this business, and under which routing policy.

    - `ai.not_configured`: AI_ROUTES_FILE is missing, unreadable or invalid, or no `agent` route has a usable
      provider at all (every provider's key variable is empty).
    - `ai.disabled_by_policy`: `agent` routes exist, but none is allowed by the effective policy.
    - `ai.budget_exceeded`: today's token budget is used up (until the next UTC day).
    `budget_percent` is today's usage against the daily budget (may exceed 100).
    """
    policy = effective_policy(db, ctx)
    percent = usage.usage_today(db, ctx).percent
    router = load_router()
    reason: str | None = None
    if router is None or not router.allowed("agent", "hosted_any"):
        reason = "ai.not_configured"
    elif not router.allowed("agent", policy):
        reason = "ai.disabled_by_policy"
    elif percent >= 100:
        reason = "ai.budget_exceeded"
    budget_enabled = usage.effective_budget(db, ctx) > 0
    classes = {}
    for model_class in get_args(ModelClass):
        available = budget_enabled and router is not None and bool(router.allowed(model_class, policy))
        detail: str | None = None
        if not available:
            if not budget_enabled:
                detail = "ai.off: zero business AI allowance"
            elif policy == "regulated":
                detail = f"regulated: no local route for {model_class}"
            elif router is None or not router.allowed(model_class, "hosted_any"):
                detail = f"ai.not_configured: no usable route for {model_class}"
            else:
                detail = f"{policy}: no allowed route for {model_class}"
        classes[model_class] = ModelClassStatus(enabled=available, reason=detail)
    return AiStatus(enabled=reason is None, reason=reason, policy=policy, budget_percent=percent, model_classes=classes)


__all__ = ["AiStatus", "DictationOut", "dictation_dictionary", "status", "transcribe_dictation", "voice_available"]

# Voice uses only this facade; implementation/recording remain owned by ai.
