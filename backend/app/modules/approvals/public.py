"""Public API of the approvals module. Other modules may import ONLY from this file."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.platform.context import Context
from app.platform.registry import ExecutionMeta, ExecutorSpec, PreviewLine

from . import service
from .events import ProposalClosed, ProposalCreated, ProposalDecided
from .schemas import Evidence, OpenProposal


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
) -> list[OpenProposal]:
    return service.find_open_proposals(
        db, ctx, conversation_id=conversation_id, action_types=action_types, item_id=item_id, invoice_id=invoice_id
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
    "Evidence",
    "ExecutionMeta",
    "ExecutorSpec",
    "OpenProposal",
    "PreviewLine",
    "ProposalClosed",
    "ProposalCreated",
    "ProposalDecided",
    "ProposalRef",
    "cancel_proposal",
    "count_pending",
    "create_proposal",
    "find_open_proposals",
    "proposal_exists",
    "top_pending",
]
