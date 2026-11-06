from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, NotFound, error_responses
from app.platform.pagination import decode_cursor
from app.platform.permissions import require

from .models import CareCase

router = APIRouter()


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    customer_id: uuid.UUID | None
    conversation_id: uuid.UUID
    source_message_id: uuid.UUID
    category: str
    priority: str
    status: str
    appointment_id: uuid.UUID | None
    invoice_id: uuid.UUID | None
    proposal_id: uuid.UUID | None
    version: int
    resolved_at: datetime | None


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


def get_case(db: Session, ctx: Context, ident: uuid.UUID) -> CareCase:
    row = db.scalar(select(CareCase).where(CareCase.business_id == ctx.business_id, CareCase.id == ident))
    if row is None:
        raise NotFound("customer_care.case_not_found")
    return row


@router.get("/customer-care/cases/{case_id}", response_model=CaseOut, responses=error_responses(401, 403, 404))
def detail(case_id: uuid.UUID, ctx: Context = Depends(require("approvals.proposal.read"))) -> CaseOut:
    with transaction(ctx) as db:
        return CaseOut.model_validate(get_case(db, ctx, case_id))


@router.post(
    "/customer-care/cases/{case_id}/resolve", response_model=CaseOut, responses=error_responses(401, 403, 404, 409)
)
def resolve(
    case_id: uuid.UUID, data: ResolveIn, ctx: Context = Depends(require("conversations.proposal.approve"))
) -> CaseOut:
    with transaction(ctx) as db:
        observed = get_case(db, ctx, case_id)
        if observed.customer_id:
            crm.lock_customers(db, ctx, {observed.customer_id})
        row = db.scalar(
            select(CareCase)
            .where(CareCase.business_id == ctx.business_id, CareCase.id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        if row.version != data.version:
            raise Conflict("customer_care.case_version_conflict", current_version=row.version)
        if row.status != "resolved":
            row.status, row.resolved_at, row.resolved_by = "resolved", clock.now(), ctx.user_id
            row.version += 1
            audit.record(
                db,
                ctx,
                action="customer_care.case_resolved",
                target_type="customer_care_case",
                target_id=row.id,
                summary="Customer Care case resolved by a person",
            )
        db.flush()
        return CaseOut.model_validate(row)


def queue_items(db: Session, ctx: Context, *, limit: int, cursor: str | None = None) -> list[dict[str, Any]]:
    stmt = select(CareCase).where(CareCase.business_id == ctx.business_id, CareCase.status == "open")
    if cursor:
        ts, rid = decode_cursor(cursor)
        stmt = stmt.where(tuple_(CareCase.created_at, CareCase.id) < tuple_(ts, rid))
    return [
        {
            "id": r.id,
            "kind": "customer_care_case",
            "title": f"Customer Care: {r.category}",
            "status": r.status,
            "created_at": r.created_at,
            "proposal_id": r.proposal_id,
            "priority": r.priority,
            "customer_id": r.customer_id,
            "conversation_id": r.conversation_id,
            "appointment_id": r.appointment_id,
            "invoice_id": r.invoice_id,
            "reasons": ["customer_care.requires_person"],
        }
        for r in db.scalars(stmt.order_by(CareCase.created_at.desc(), CareCase.id.desc()).limit(limit))
    ]
