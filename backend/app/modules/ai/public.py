"""Public API of the ai module. Other modules may import ONLY from this file."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform.context import Context

from . import usage
from .gateway.router import effective_policy, load_router
from .schemas import AiStatus


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
    return AiStatus(enabled=reason is None, reason=reason, policy=policy, budget_percent=percent)


__all__ = ["AiStatus", "status"]
