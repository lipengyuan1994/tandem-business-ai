"""Read-only feed of supplier bills, bill payments and suppliers for bookkeeping sync (accounting module).

Rows carry their `version`, which changes whenever the row changes. Callers own the transaction.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.platform.context import Context

from . import permissions as P
from .models import PurchaseOrder, Supplier, SupplierBill, SupplierBillPayment

MAX_IDS = 500


class LedgerSupplier(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None


class LedgerBill(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    supplier_id: uuid.UUID
    supplier_name: str
    supplier_reference: str | None
    purchase_order_number: str | None
    bill_date: date
    due_date: date | None
    currency: str
    total_minor: int


class LedgerBillPayment(BaseModel):
    id: uuid.UUID
    version: int
    bill_id: uuid.UUID
    supplier_id: uuid.UUID
    currency: str
    amount_minor: int
    paid_on: date
    method: str
    reference: str | None
    voided: bool


def bill_versions(db: Session, ctx: Context, since: date) -> dict[uuid.UUID, int]:
    """Bills dated on or after `since`, void ones included: id -> version."""
    ctx.require(P.BILL_READ)
    rows = db.execute(
        select(SupplierBill.id, SupplierBill.version).where(
            SupplierBill.business_id == ctx.business_id, SupplierBill.bill_date >= since
        )
    )
    return {row.id: row.version for row in rows}


def bills(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, LedgerBill]:
    ctx.require(P.BILL_READ)
    if not ids:
        return {}
    rows = db.execute(
        select(SupplierBill, PurchaseOrder.number)
        .outerjoin(
            PurchaseOrder,
            (PurchaseOrder.business_id == SupplierBill.business_id)
            & (PurchaseOrder.id == SupplierBill.purchase_order_id),
        )
        .where(SupplierBill.business_id == ctx.business_id, SupplierBill.id.in_(list(ids)[:MAX_IDS]))
    ).all()
    return {
        bill.id: LedgerBill(
            id=bill.id,
            version=bill.version,
            status=bill.status,
            supplier_id=bill.supplier_id,
            supplier_name=bill.supplier_name,
            supplier_reference=bill.supplier_reference,
            purchase_order_number=po_number,
            bill_date=bill.bill_date,
            due_date=bill.due_date,
            currency=bill.currency,
            total_minor=bill.total_minor,
        )
        for bill, po_number in rows
    }


def bill_payment_versions(db: Session, ctx: Context, since: date) -> dict[uuid.UUID, int]:
    """Bill payments made on or after `since`, or against a bill dated on or after it (a payment can be dated before
    its bill, such as a deposit), voided ones included: id -> version."""
    ctx.require(P.BILL_READ)
    rows = db.execute(
        select(SupplierBillPayment.id, SupplierBillPayment.version)
        .join(
            SupplierBill,
            (SupplierBill.business_id == SupplierBillPayment.business_id)
            & (SupplierBill.id == SupplierBillPayment.bill_id),
        )
        .where(
            SupplierBillPayment.business_id == ctx.business_id,
            or_(SupplierBillPayment.paid_on >= since, SupplierBill.bill_date >= since),
        )
    )
    return {row.id: row.version for row in rows}


def bill_payments(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, LedgerBillPayment]:
    ctx.require(P.BILL_READ)
    if not ids:
        return {}
    rows = db.execute(
        select(SupplierBillPayment, SupplierBill.supplier_id, SupplierBill.currency)
        .join(
            SupplierBill,
            (SupplierBill.business_id == SupplierBillPayment.business_id)
            & (SupplierBill.id == SupplierBillPayment.bill_id),
        )
        .where(SupplierBillPayment.business_id == ctx.business_id, SupplierBillPayment.id.in_(list(ids)[:MAX_IDS]))
    ).all()
    return {
        payment.id: LedgerBillPayment(
            id=payment.id,
            version=payment.version,
            bill_id=payment.bill_id,
            supplier_id=supplier_id,
            currency=currency,
            amount_minor=payment.amount_minor,
            paid_on=payment.paid_on,
            method=payment.method,
            reference=payment.reference,
            voided=payment.voided_at is not None,
        )
        for payment, supplier_id, currency in rows
    }


def suppliers(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, LedgerSupplier]:
    """Name and order e-mail of the given suppliers; bill readers may see them."""
    ctx.require(P.BILL_READ)
    if not ids:
        return {}
    rows = db.scalars(
        select(Supplier).where(Supplier.business_id == ctx.business_id, Supplier.id.in_(list(ids)[:MAX_IDS]))
    )
    return {row.id: LedgerSupplier(id=row.id, name=row.name, email=row.email) for row in rows}
