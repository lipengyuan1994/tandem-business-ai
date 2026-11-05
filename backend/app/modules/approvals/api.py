"""/api/v1/approvals."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import error_responses
from app.platform.permissions import require
from app.platform.registry import get_registry

from . import permissions as P
from . import service
from .schemas import (
    ActionTypeOut,
    AttemptOut,
    DecisionCreate,
    DecisionOut,
    ProposalCreate,
    ProposalDetailOut,
    ProposalOut,
    ProposalPage,
    RevisionCreate,
    RevisionOut,
)

router = APIRouter()
ERRORS = error_responses(401, 403, 404, 409, 422)


def _out(ctx: Context, p: Any) -> ProposalOut:
    out = ProposalOut.model_validate(p)
    out.can_decide = service.can_decide(ctx, p)
    return out


def _detail(db: Any, ctx: Context, proposal_id: uuid.UUID) -> ProposalDetailOut:
    d = service.get_detail(db, ctx, proposal_id)
    base = _out(ctx, d["proposal"]).model_dump()
    return ProposalDetailOut(
        **base,
        current_revision=RevisionOut.model_validate(d["current_revision"]),
        revisions=[RevisionOut.model_validate(r) for r in d["revisions"]],
        decisions=[
            DecisionOut.model_validate(dec).model_copy(
                update={"decided_by_name": name, "granted_by_name": d["grantor_names"][dec.id]}
            )
            for dec, name in d["decisions"]
        ],
        attempts=[AttemptOut.model_validate(a) for a in d["attempts"]],
    )


@router.get("/proposals", response_model=ProposalPage, summary="Proposals, newest first", responses=ERRORS)
def list_proposals(
    status: str | None = None,
    action_type: str | None = None,
    mine: bool = False,
    can_approve: bool = False,
    limit: int = 50,
    cursor: str | None = None,
    ctx: Context = Depends(require(P.PROPOSAL_READ)),
) -> ProposalPage:
    with transaction(ctx) as db:
        rows, next_cursor = service.list_proposals(
            db,
            ctx,
            status=status,
            action_type=action_type,
            mine=mine,
            can_approve=can_approve,
            limit=limit,
            cursor=cursor,
        )
        return ProposalPage(items=[_out(ctx, r) for r in rows], next_cursor=next_cursor)


@router.get(
    "/proposals/{proposal_id}", response_model=ProposalDetailOut, summary="Proposal with history", responses=ERRORS
)
def get_proposal(proposal_id: uuid.UUID, ctx: Context = Depends(require(P.PROPOSAL_READ))) -> ProposalDetailOut:
    with transaction(ctx) as db:
        return _detail(db, ctx, proposal_id)


@router.post(
    "/proposals", response_model=ProposalDetailOut, status_code=201, summary="Create a staff proposal", responses=ERRORS
)
def create_proposal(body: ProposalCreate, ctx: Context = Depends(require(P.PROPOSAL_CREATE))) -> ProposalDetailOut:
    with transaction(ctx) as db:
        p = service.create_proposal(
            db, ctx, action_type=body.action_type, payload=body.payload, title=body.title, evidence=body.evidence
        )
        return _detail(db, ctx, p.id)


@router.post(
    "/proposals/{proposal_id}/revisions",
    response_model=ProposalDetailOut,
    summary="Edit: creates a new revision",
    responses=ERRORS,
)
def revise(
    proposal_id: uuid.UUID, body: RevisionCreate, ctx: Context = Depends(require(P.PROPOSAL_READ))
) -> ProposalDetailOut:
    with transaction(ctx) as db:
        service.revise(db, ctx, proposal_id, body.base_revision_id, body.payload)
        return _detail(db, ctx, proposal_id)


@router.post(
    "/proposals/{proposal_id}/decisions",
    response_model=ProposalDetailOut,
    summary="Approve or reject the current revision",
    responses=ERRORS,
)
def decide(
    proposal_id: uuid.UUID, body: DecisionCreate, ctx: Context = Depends(require(P.PROPOSAL_READ))
) -> ProposalDetailOut:
    with transaction(ctx) as db:
        service.decide(
            db,
            ctx,
            proposal_id,
            decision=body.decision,
            revision_id=body.revision_id,
            payload_hash=body.payload_hash,
            comment=body.comment,
        )
        return _detail(db, ctx, proposal_id)


@router.post(
    "/proposals/{proposal_id}/cancel",
    response_model=ProposalDetailOut,
    summary="Cancel a pending proposal",
    responses=ERRORS,
)
def cancel(proposal_id: uuid.UUID, ctx: Context = Depends(require(P.PROPOSAL_READ))) -> ProposalDetailOut:
    with transaction(ctx) as db:
        service.cancel(db, ctx, proposal_id)
        return _detail(db, ctx, proposal_id)


@router.get(
    "/action-types",
    response_model=list[ActionTypeOut],
    summary="Registered action types and payload schemas",
    responses=ERRORS,
)
def action_types(ctx: Context = Depends(require(P.PROPOSAL_READ))) -> list[ActionTypeOut]:
    return [
        ActionTypeOut(
            action_type=s.action_type,
            approve_permission=s.approve_permission,
            payload_schema=s.payload_model.model_json_schema(),
        )
        for s in sorted(get_registry().executors.values(), key=lambda s: s.action_type)
    ]
