"""Reconnect durable domain subjects to the execution that created them (e.g. supplier waiting)."""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.platform.context import Context

from .models import ActionProposal, ExecutionAttempt


def workflow_for_result(db: Session, ctx: Context, field: str, ident: UUID) -> dict[str, Any] | None:
    if field not in {"quote_request_id", "purchase_order_id", "reorder_draft_id"}:
        raise ValueError("unsupported workflow subject")
    proposal = db.scalar(
        select(ActionProposal)
        .join(
            ExecutionAttempt,
            and_(
                ExecutionAttempt.business_id == ActionProposal.business_id,
                ExecutionAttempt.proposal_id == ActionProposal.id,
            ),
        )
        .where(
            ActionProposal.business_id == ctx.business_id,
            ActionProposal.correlation_id.is_not(None),
            ExecutionAttempt.status == "succeeded",
            ExecutionAttempt.result.contains({field: str(ident)}),
        )
        .order_by(ExecutionAttempt.finished_at, ExecutionAttempt.id)
        .limit(1)
    )
    if proposal is None:
        return None
    return {
        "correlation_id": proposal.correlation_id,
        "causation_id": proposal.id,
        "agent_key": proposal.agent_key,
        "chain_depth": proposal.chain_depth or 0,
    }
