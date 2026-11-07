from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select, tuple_
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform import audit, clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict, Invalid, NotFound, error_responses
from app.platform.pagination import decode_cursor, encode_cursor
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


PRIORITIES = {"urgent": 2, "high": 1, "normal": 0}


def queue_cursor(priority: str, created_at: datetime, ident: uuid.UUID) -> str:
    raw = json.dumps([PRIORITIES[priority], encode_cursor(created_at, ident)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def queue_items(db: Session, ctx: Context, *, limit: int, cursor: str | None = None) -> list[dict[str, Any]]:
    rank = case(PRIORITIES, value=CareCase.priority, else_=0)
    stmt = select(CareCase).where(CareCase.business_id == ctx.business_id, CareCase.status == "open")
    if cursor:
        try:
            priority, keyset = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if type(priority) is not int or priority not in PRIORITIES.values() or not isinstance(keyset, str):
                raise ValueError
        except (ValueError, TypeError, binascii.Error):
            raise Invalid("platform.invalid_cursor") from None
        ts, rid = decode_cursor(keyset)
        stmt = stmt.where(tuple_(rank, CareCase.created_at, CareCase.id) < tuple_(priority, ts, rid))

    rows = list(db.scalars(stmt.order_by(rank.desc(), CareCase.created_at.desc(), CareCase.id.desc()).limit(limit)))
    names = (
        crm.customer_names(db, ctx, {r.customer_id for r in rows if r.customer_id})
        if ctx.has("crm.customer.read")
        else {}
    )
    return [
        {
            "id": r.id,
            "kind": "customer_care_case",
            "title": f"Customer Care: {r.category}",
            "status": r.status,
            "created_at": r.created_at,
            "proposal_id": r.proposal_id,
            "priority": r.priority,
            "category": r.category,
            "version": r.version,
            "customer_name": names.get(r.customer_id) if r.customer_id else None,
            "customer_id": r.customer_id,
            "conversation_id": r.conversation_id,
            "appointment_id": r.appointment_id,
            "invoice_id": r.invoice_id,
            "reasons": ["customer_care.requires_person"],
        }
        for r in rows
    ]
