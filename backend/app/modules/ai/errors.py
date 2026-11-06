"""AiError (docs/roadmap/ai/contracts.md §15): one DomainError with a fixed set of codes and their HTTP statuses."""

from __future__ import annotations

from typing import Any

from app.platform.errors import DomainError

# code -> (HTTP status, problem title). Codes without a mapping in §15 are internal (runner, tool observations).
AI_ERROR_CODES: dict[str, tuple[int, str]] = {
    "ai.agent_disabled": (409, "AI agent is off"),
    "ai.chain_transition_blocked": (409, "Agent hand-off is not allowed"),
    "ai.chain_depth_exceeded": (409, "Chain depth limit reached"),
    "ai.chain_call_limit": (429, "Chain model call limit reached"),
    "ai.chain_action_limit": (429, "Chain action limit reached"),
    "ai.chain_cost_limit": (429, "Chain cost limit reached"),
    "ai.agent_limit_reached": (429, "Agent daily cost limit reached"),
    "ai.conversation_paused": (409, "Conversation handed to a person"),
    "ai.disabled_by_policy": (409, "Conflict"),
    "ai.not_configured": (409, "Conflict"),
    "ai.budget_exceeded": (429, "Too many requests"),
    "ai.provider_unavailable": (503, "Service unavailable"),
    "ai.invalid_model_output": (502, "Bad gateway"),
    "ai.step_limit": (502, "Bad gateway"),
    "ai.run_timeout": (502, "Bad gateway"),
    "ai.tool_not_allowed": (400, "Bad request"),
    "ai.interrupted": (400, "Bad request"),
    "ai.input_too_large": (413, "Input too large"),
}


class AiError(DomainError):
    def __init__(self, code: str, detail: str | None = None, **extra: Any) -> None:
        if code not in AI_ERROR_CODES:
            raise ValueError(f"unknown ai error code {code!r}")
        super().__init__(code, detail, **extra)
        self.status, self.title = AI_ERROR_CODES[code]
