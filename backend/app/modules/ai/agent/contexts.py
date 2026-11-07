"""Who acts in a run (contracts §3, §8; architecture §4). A factory turns the run id into the run's Context."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace

from app.platform.context import Context, assistant_context, workflow_context
from app.platform.errors import Forbidden

from ..permissions import AI_SYSTEM_PERMISSIONS, AI_TOOL_PERMISSIONS, ASSISTANT_USE, USE_CASE_EXTRA_PERMISSIONS
from ..schemas import UseCaseId

CtxFactory = Callable[[uuid.UUID], Context]  # run_id → context


def background_ctx_factory(
    business_id: uuid.UUID, *, use_case: UseCaseId | None = None, parent: Context | None = None
) -> CtxFactory:
    """System permissions, plus the owner-approved U10 reminder-proposal permission only."""
    permissions = AI_SYSTEM_PERMISSIONS
    if use_case == "payment_followup":
        permissions |= USE_CASE_EXTRA_PERMISSIONS[use_case]

    return _factory(business_id, permissions, None, use_case, parent)


def _factory(
    business_id: uuid.UUID,
    permissions: frozenset[str],
    user_id: uuid.UUID | None,
    use_case: UseCaseId | None,
    parent: Context | None,
) -> CtxFactory:
    from ..agents import USE_CASE_OWNERS

    parent = parent or workflow_context()
    if parent is not None and parent.business_id != business_id:
        parent = None
    correlation_id = (parent.correlation_id if parent else None) or uuid.uuid4()
    agent = USE_CASE_OWNERS.get(use_case or "")
    if use_case == "copilot" and parent is not None and parent.agent_key is not None:
        agent = parent.agent_key

    def factory(run_id: uuid.UUID) -> Context:
        return replace(
            assistant_context(business_id, permissions, on_behalf_of_user_id=user_id, ai_run_id=run_id),
            correlation_id=correlation_id,
            causation_id=parent.causation_id if parent else None,
            agent_key=agent
            or (parent.agent_key if parent and use_case in {"safety", "grounding", "reply_guardrail"} else None),
            causing_agent_key=parent.agent_key if parent else None,
            ai_manager_required=use_case == "copilot",
            chain_depth=(parent.chain_depth if parent else 0) + 1,
        )

    return factory


def staff_permissions(user_permissions: frozenset[str], use_case: UseCaseId) -> frozenset[str]:
    """user_permissions ∩ (AI_TOOL_PERMISSIONS ∪ USE_CASE_EXTRA_PERMISSIONS[use_case]) ∪ {"ai.assistant.use"};
    the user must hold ai.assistant.use. Never more than the user has."""
    if ASSISTANT_USE not in user_permissions:
        raise Forbidden("auth.permission_denied", detail=ASSISTANT_USE)
    # The U10 exception is for background reminder proposals only, never staff-triggered contexts.
    extra = frozenset() if use_case == "payment_followup" else USE_CASE_EXTRA_PERMISSIONS.get(use_case, frozenset())
    upper = AI_TOOL_PERMISSIONS | extra
    return (user_permissions & upper) | {ASSISTANT_USE}


def staff_ctx_factory(
    business_id: uuid.UUID,
    user_id: uuid.UUID,
    user_permissions: frozenset[str],
    use_case: UseCaseId,
    *,
    parent: Context | None = None,
) -> CtxFactory:
    """Staff-requested runs (copilot, summary, campaign draft): acts on behalf of the user, never with more rights."""
    permissions = staff_permissions(frozenset(user_permissions), use_case)

    return _factory(business_id, permissions, user_id, use_case, parent)


__all__ = ["CtxFactory", "background_ctx_factory", "staff_ctx_factory", "staff_permissions"]
