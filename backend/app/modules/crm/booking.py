"""Booking eligibility synchronized with retention holds, without changing standard lookups."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.platform.config import is_regulated
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound

from . import service
from .models import Customer
from .schemas import CustomerSummary


def customer_for_booking(db: Session, ctx: Context, customer_id: uuid.UUID) -> CustomerSummary:
    lock_customers(db, ctx, {customer_id})
    customer = service.summary(db, ctx, customer_id)
    if is_regulated() and customer.status != "active":
        raise Conflict("crm.customer_archived")
    return customer


def lock_customers(db: Session, ctx: Context, customer_ids: set[uuid.UUID]) -> None:
    """Customer before conversation; share booking's advisory lock before any row lock upgrade."""
    for customer_id in sorted(customer_ids):
        db.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"booking:{ctx.business_id}:{customer_id}"},
        )
        row = db.scalar(
            select(Customer)
            .where(
                Customer.business_id == ctx.business_id,
                Customer.id == customer_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("crm.customer_not_found")
