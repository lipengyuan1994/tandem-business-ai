"""Request context: who is acting, for which business, with which permissions."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import UUID

from app.platform.errors import Forbidden

ActorKind = Literal["user", "assistant", "system", "visitor"]


@dataclass(frozen=True, slots=True)
class Context:
    request_id: str
    actor_kind: ActorKind
    user_id: UUID | None
    business_id: UUID | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    session_id: UUID | None = None
    on_behalf_of_user_id: UUID | None = None
    ai_run_id: UUID | None = None
    proposal_id: UUID | None = None
    visitor_conversation_id: UUID | None = None
    autonomy_rule_id: UUID | None = None
    autonomy_rule_version: int | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    agent_key: str | None = None
    causing_agent_key: str | None = None
    ai_manager_required: bool = False
    chain_depth: int = 0

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise Forbidden("auth.permission_denied", detail=permission)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def with_proposal(self, proposal_id: UUID) -> Context:
        return replace(self, proposal_id=proposal_id)


# A worker's immutable incoming context. ContextVar isolates simultaneous jobs/threads; factories capture
# this value explicitly before a copilot worker is launched. Tokens are always reset, including on failure.
_workflow: ContextVar[Context | None] = ContextVar("workflow_context", default=None)


def workflow_context() -> Context | None:
    return _workflow.get()


@contextmanager
def workflow_scope(ctx: Context) -> Iterator[None]:
    token = _workflow.set(ctx)
    try:
        yield
    finally:
        _workflow.reset(token)


def inherit_workflow(ctx: Context, parent: Context | None) -> Context:
    if parent is None or parent.business_id != ctx.business_id:
        return ctx
    return replace(
        ctx,
        correlation_id=parent.correlation_id,
        causation_id=parent.causation_id,
        agent_key=parent.agent_key,
        chain_depth=parent.chain_depth,
    )


def system_context(business_id: UUID | None, permissions: frozenset[str] = frozenset()) -> Context:
    """Context for jobs and event handlers. Permissions are empty unless a handler needs specific ones."""
    return Context(
        request_id=f"sys-{uuid.uuid4().hex[:16]}",
        actor_kind="system",
        user_id=None,
        business_id=business_id,
        permissions=permissions,
    )


def assistant_context(
    business_id: UUID, permissions: frozenset[str], *, on_behalf_of_user_id: UUID | None, ai_run_id: UUID
) -> Context:
    return Context(
        request_id=f"ai-{uuid.uuid4().hex[:16]}",
        actor_kind="assistant",
        user_id=None,
        business_id=business_id,
        permissions=permissions,
        on_behalf_of_user_id=on_behalf_of_user_id,
        ai_run_id=ai_run_id,
    )
