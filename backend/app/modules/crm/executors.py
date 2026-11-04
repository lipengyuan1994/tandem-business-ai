"""Approved-action executors owned by crm (07-workflow-engine.md#mvp-action-types)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.approvals.public import ExecutionMeta, ExecutorSpec, PreviewLine
from app.platform import crud
from app.platform.context import Context
from app.platform.errors import Conflict

from . import permissions as P
from . import service
from .models import Customer
from .schemas import CustomerCreate, UpdateCustomerPayload


def _describe_create(db: Session, ctx: Context, p: CustomerCreate) -> list[PreviewLine]:
    lines = [PreviewLine("New customer", None, p.display_name)]
    lines += [PreviewLine(cp.type.title(), None, cp.value) for cp in p.contact_points]
    lines += [PreviewLine(f"Consent {c.channel}/{c.purpose}", None, c.status) for c in p.consents]
    return lines


def _validate_create(db: Session, ctx: Context, p: CustomerCreate) -> None:
    for cp in p.contact_points:
        if service.find_candidates(
            db, ctx, email=cp.value if cp.type == "email" else None, phone=cp.value if cp.type == "phone" else None
        ):
            raise Conflict("crm.contact_point_in_use", detail=f"{cp.type} belongs to an existing customer")


def _run_create(db: Session, ctx: Context, p: CustomerCreate, meta: ExecutionMeta) -> dict[str, Any]:
    return {"customer_id": str(service.create_customer(db, ctx, p).id)}


def _describe_update(db: Session, ctx: Context, p: UpdateCustomerPayload) -> list[PreviewLine]:
    current = crud.get_scoped(db, ctx, Customer, p.customer_id)
    return [
        PreviewLine(k.replace("_", " ").title(), str(getattr(current, k, "")) if current else None, str(v))
        for k, v in p.changes.model_dump(exclude_unset=True).items()
    ]


def _validate_update(db: Session, ctx: Context, p: UpdateCustomerPayload) -> None:
    current = crud.require_scoped(db, ctx, Customer, p.customer_id, "crm.customer_not_found")
    if current.version != p.customer_version:
        raise Conflict("crm.customer_version_conflict", current_version=current.version)


def _run_update(db: Session, ctx: Context, p: UpdateCustomerPayload, meta: ExecutionMeta) -> dict[str, Any]:
    return {"customer_id": str(service.update_customer(db, ctx, p.customer_id, p.customer_version, p.changes).id)}


CREATE = ExecutorSpec(
    "crm.create_customer", CustomerCreate, P.PROPOSAL_APPROVE, _describe_create, _validate_create, _run_create
)
UPDATE = ExecutorSpec(
    "crm.update_customer", UpdateCustomerPayload, P.PROPOSAL_APPROVE, _describe_update, _validate_update, _run_update
)
