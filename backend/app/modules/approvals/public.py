"""Public API of the approvals module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.registry import ExecutionMeta, ExecutorSpec, PreviewLine

from . import service
from .autonomy import RULE_ELIGIBLE_ACTIONS as RULE_ELIGIBLE_ACTIONS
from .autonomy_schemas import ActivityPage, PauseOut, RuleOut, RulePut, RulesOut
from .autonomy_service import (
    activity as autonomy_activity,
)
from .autonomy_service import (
    list_rules as list_autonomy_rules,
)
from .autonomy_service import (
    put_rule as put_autonomy_rule,
)
from .autonomy_service import (
    rule_out as autonomy_rule_out,
)
from .chat import card as chat_card
from .chat import confirm as confirm_chat
from .chat import origin_run_id as chat_origin_run_id
from .events import ProposalClosed, ProposalCreated, ProposalDecided
from .invalidation import invalidate_ai_replies as invalidate_ai_replies
from .lineage import workflow_for_result as workflow_for_result
from .queue import fallback_items as fallback_queue_items
from .queue import proposal_items as proposal_queue_items
from .reading import outbound_authority, policy_reply_payload, proposal_notification_state
from .schemas import ChatCard, ChatConfirmIn, Evidence, OpenProposal
from .team import activity as team_activity
from .team import return_items as team_return_items
from .team import summary as team_summary


class ProposalRef:
    def __init__(self, proposal_id: uuid.UUID, status: str, current_revision_id: uuid.UUID | None) -> None:
        self.id, self.status, self.current_revision_id = proposal_id, status, current_revision_id


def create_proposal(
    db: Session,
    ctx: Context,
    *,
    action_type: str,
    payload: dict[str, Any],
    title: str,
    evidence: list[Evidence] | None = None,
    expires_in: timedelta | None = None,
    conversation_id: uuid.UUID | None = None,
) -> ProposalRef:
    p = service.create_proposal(
        db,
        ctx,
        action_type=action_type,
        payload=payload,
        title=title,
        evidence=evidence,
        expires_in=expires_in,
        conversation_id=conversation_id,
    )
    return ProposalRef(p.id, p.status, p.current_revision_id)


def cancel_proposal(db: Session, ctx: Context, proposal_id: uuid.UUID) -> None:
    service.cancel(db, ctx, proposal_id)


def proposal_exists(db: Session, ctx: Context, proposal_id: uuid.UUID) -> bool:
    """Check a reference in the current business, without exposing the proposal's content or status."""
    return service.proposal_exists(db, ctx, proposal_id)


def find_open_proposals(
    db: Session,
    ctx: Context,
    *,
    conversation_id: uuid.UUID | None = None,
    action_types: list[str] | None = None,
    item_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    include_executing: bool = False,
) -> list[OpenProposal]:
    return service.find_open_proposals(
        db,
        ctx,
        conversation_id=conversation_id,
        action_types=action_types,
        item_id=item_id,
        invoice_id=invoice_id,
        include_executing=include_executing,
    )


def top_pending(db: Session, ctx: Context, limit: int = 5) -> list[dict[str, Any]]:
    """Newest pending proposals the current user may decide: id, title, action_type, origin_kind, created_at."""
    rows, _ = service.list_proposals(db, ctx, status=None, action_type=None, mine=False, can_approve=True, limit=limit)
    return [
        {
            "id": r.id,
            "title": r.title,
            "action_type": r.action_type,
            "origin_kind": r.origin_kind,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def count_pending(db: Session, ctx: Context, *, for_permissions: frozenset[str] | None = None) -> int:
    return service.count_pending(db, ctx, for_permissions=for_permissions)


__all__ = [
    "RULE_ELIGIBLE_ACTIONS",
    "ActivityPage",
    "ChatCard",
    "ChatConfirmIn",
    "Evidence",
    "ExecutionMeta",
    "ExecutorSpec",
    "OpenProposal",
    "PauseOut",
    "PreviewLine",
    "ProposalClosed",
    "ProposalCreated",
    "ProposalDecided",
    "ProposalRef",
    "RuleOut",
    "RulePut",
    "RulesOut",
    "autonomy_activity",
    "autonomy_rule_out",
    "cancel_proposal",
    "chat_card",
    "chat_origin_run_id",
    "check_ai_proposal_allowed",
    "confirm_chat",
    "count_pending",
    "create_proposal",
    "fallback_queue_items",
    "find_open_proposals",
    "invalidate_ai_replies",
    "list_autonomy_rules",
    "outbound_authority",
    "policy_reply_payload",
    "proposal_exists",
    "proposal_notification_state",
    "proposal_queue_items",
    "put_autonomy_rule",
    "team_activity",
    "team_return_items",
    "team_summary",
    "top_pending",
    "workflow_for_result",
]


def check_ai_proposal_allowed(db: Session, ctx: Context, action_type: str) -> None:
    """Optional early off-rule check to avoid generation cost; create_proposal always re-checks."""
    from .autonomy import creation_rule

    creation_rule(db, ctx, action_type)
