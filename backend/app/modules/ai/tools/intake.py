"""Internal intake completion invariant and content-free model-output diagnostics. No HTTP schema changes."""

from __future__ import annotations

import uuid

from pydantic import ValidationError

from ..agent.protocol import AGENT_STEP_ADAPTER, FinalStep, RunSpec, ToolCallStep
from .base import RunState

REPLY_REQUIRED = (
    "Visitor intake must call propose_reply (or propose_booking with its reply) before final. "
    "A final is staff-only, not a customer reply. If a person is needed, call propose_reply with request_handoff=true. "
    "Return a tool_call JSON object with kind, tool, arguments and reason."
)
SEARCH_COMPLETE = (
    "search_knowledge is complete (empty, repeated or two lookups). Do not search again. "
    "Call propose_reply with relevant latest article-id citations, or knowledge_missing=true and no citations "
    "if no passage answers. Return kind=tool_call, tool=propose_reply, arguments and reason."
)


def validate_step(
    text: str, spec: RunSpec, proposals: list[uuid.UUID], state: RunState | None = None
) -> FinalStep | ToolCallStep:
    step = AGENT_STEP_ADAPTER.validate_json(text)
    if (
        isinstance(step, FinalStep)
        and spec.use_case == "intake"
        and spec.trigger_message_id
        and not proposals
        and step.care_category == "none"
    ):
        raise ValueError(REPLY_REQUIRED)
    if isinstance(step, ToolCallStep) and spec.use_case == "intake" and step.tool == "search_knowledge" and state:
        query = step.arguments.get("query")
        repeated = isinstance(query, str) and " ".join(query.casefold().split()) == state.knowledge_last_query
        if state.knowledge_search_complete or state.knowledge_search_count >= 2 or repeated:
            state.knowledge_search_complete = True
            raise ValueError(SEARCH_COMPLETE)
    return step


def validation_summary(exc: ValueError) -> str:
    """Error types and known protocol fields only; never model-written keys/values/discriminator tags."""
    if isinstance(exc, ValidationError):
        fields = {
            "tool_call",
            "final",
            "kind",
            "tool",
            "arguments",
            "reason",
            "message_to_staff",
            "proposal_ids",
            "needs_human",
            "citations",
            "article_id",
            "version",
            "text",
            "conversation_id",
            "claims",
            "verdict",
            "source",
            "overall_verdict",
            "confidence",
            "explanation",
            "analysis",
            "name",
            "type",
            "function",
            "parameters",
            "answer",
            "tool_call_id",
            "output",
        }
        errors = set()
        for error in exc.errors(include_input=False):
            location = ".".join(
                str(p) if p in fields or (isinstance(p, int) and 0 <= p <= 8) else "field" for p in error["loc"]
            )
            errors.add(error["type"] + ("@" + location if location else ""))
        return "validation: " + ",".join(sorted(errors)[:8])[:200]
    return "validation: " + (
        "intake_reply_required"
        if str(exc) == REPLY_REQUIRED
        else "knowledge_search_complete"
        if str(exc) == SEARCH_COMPLETE
        else "invalid_step"
    )
