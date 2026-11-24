"""Supplier bills (accounts payable) and the payments made against them.

`supplier_bills.amount_paid_minor` always equals the sum of the bill's non-voided payments, and the status follows it:
open → partially_paid → paid. Payments never exceed the open balance (no supplier credit balances yet). A bill with no
live payments can be voided; a payment recorded in error is voided, never deleted, and reopens the bill.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.catalog import public as catalog
from app.modules.org import public as org
from app.platform import audit, clock, crud
from app.platform.context import Context
from app.platform.errors import Conflict, Invalid, NotFound

from . import permissions as P
from . import repository as repo
from .models import PurchaseOrder, SupplierBill, SupplierBillPayment
from .schemas_bills import (
    BillIn,
    BillOut,
    BillPaymentIn,
    BillPaymentOut,
    BillStatus,
    BillUpdate,
    BillVoidIn,
    PurchaseOrderBilling,
)

BILLABLE_PO_STATUSES = ("approved", "sent", "partially_received", "received")


def business_today(db: Session, ctx: Context) -> date:
    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def get(db: Session, ctx: Context, bill_id: uuid.UUID, *, lock: bool = False) -> SupplierBill:
    return crud.require_scoped(db, ctx, SupplierBill, bill_id, "purchasing.bill_not_found", for_update=lock)


def balance(row: SupplierBill) -> int:
    return 0 if row.status == "void" else row.total_minor - row.amount_paid_minor


def _status_after_payments(row: SupplierBill) -> str:
    if row.amount_paid_minor == 0:
        return "open"
    return "paid" if row.amount_paid_minor == row.total_minor else "partially_paid"


def check_version(row: SupplierBill, version: int) -> None:
    if row.version != version:
        raise Conflict("purchasing.version_conflict", current_version=row.version)


def _record(db: Session, ctx: Context, row: SupplierBill, action: str, **changes: object) -> None:
    db.flush()
    audit.record(
        db,
        ctx,
        action=f"purchasing.bill.{action}",
        target_type="purchasing.bill",
        target_id=row.id,
        summary=f"Supplier bill {action.replace('_', ' ')}",
        changes={"status": row.status, "version": row.version, **changes},
    )


def _check_bill_date(db: Session, ctx: Context, bill_date: date) -> None:
    if bill_date > business_today(db, ctx):
        raise Invalid("purchasing.bill_date_in_future")


def _check_duplicate(
    db: Session, ctx: Context, supplier_id: uuid.UUID, reference: str | None, exclude: uuid.UUID | None = None
) -> None:
    """One live bill per supplier invoice number, so the same paper bill is not entered (and paid) twice."""
    if reference is None:
        return
    stmt = select(SupplierBill.id).where(
        SupplierBill.business_id == ctx.business_id,
        SupplierBill.supplier_id == supplier_id,
        func.lower(SupplierBill.supplier_reference) == reference.lower(),
        SupplierBill.status != "void",
    )
    if exclude is not None:
        stmt = stmt.where(SupplierBill.id != exclude)
    existing = db.scalar(stmt.limit(1))
    if existing is not None:
        raise Conflict(
            "purchasing.duplicate_bill",
            detail="This supplier already has a bill with that reference",
            bill_id=str(existing),
        )


def _purchase_order(db: Session, ctx: Context, supplier_id: uuid.UUID, po_id: uuid.UUID) -> PurchaseOrder:
    po = repo.order(db, ctx, po_id)
    if po.supplier_id != supplier_id:
        raise Invalid("purchasing.bill_po_supplier_mismatch", detail="The purchase order is for another supplier")
    if po.status not in BILLABLE_PO_STATUSES:
        raise Conflict("purchasing.bill_po_not_billable", detail="Bill approved, sent or received purchase orders only")
    return po


def create(db: Session, ctx: Context, data: BillIn) -> SupplierBill:
    ctx.require(P.BILL_WRITE)
    supplier = repo.supplier(db, ctx, data.supplier_id)
    _check_bill_date(db, ctx, data.bill_date)
    currency = catalog.get_default_currency(db, ctx)
    if data.purchase_order_id is not None:
        currency = _purchase_order(db, ctx, supplier.id, data.purchase_order_id).currency
    # Serialize bill entry per supplier so two people cannot record the same supplier reference at once.
    repo.supplier(db, ctx, supplier.id, lock=True)
    _check_duplicate(db, ctx, supplier.id, data.supplier_reference)
    row = SupplierBill(
        business_id=ctx.business_id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        purchase_order_id=data.purchase_order_id,
        supplier_reference=data.supplier_reference,
        bill_date=data.bill_date,
        due_date=data.due_date,
        currency=currency,
        total_minor=data.total_minor,
        amount_paid_minor=0,
        status="open",
        notes=data.notes,
        created_by=ctx.user_id,
    )
    db.add(row)
    _record(db, ctx, row, "created", total_minor=row.total_minor)
    return row


def update(db: Session, ctx: Context, bill_id: uuid.UUID, data: BillUpdate) -> SupplierBill:
    ctx.require(P.BILL_WRITE)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, data.version)
    if row.status != "open":
        raise Conflict("purchasing.bill_locked", detail="Only open bills with no payments can be edited")
    fields = data.model_fields_set - {"version"}
    if "bill_date" in fields and data.bill_date is not None:
        _check_bill_date(db, ctx, data.bill_date)
        row.bill_date = data.bill_date
    if "due_date" in fields:
        row.due_date = data.due_date
    if row.due_date is not None and row.due_date < row.bill_date:
        raise Invalid("purchasing.bill_due_before_bill_date")
    if "supplier_reference" in fields:
        repo.supplier(db, ctx, row.supplier_id, lock=True)
        _check_duplicate(db, ctx, row.supplier_id, data.supplier_reference, exclude=row.id)
        row.supplier_reference = data.supplier_reference
    if "total_minor" in fields and data.total_minor is not None:
        row.total_minor = data.total_minor
    if "notes" in fields:
        row.notes = data.notes
    row.version += 1
    _record(db, ctx, row, "updated", fields=sorted(fields))
    return row


def void(db: Session, ctx: Context, bill_id: uuid.UUID, data: BillVoidIn) -> SupplierBill:
    ctx.require(P.BILL_VOID)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, data.version)
    if row.status == "void":
        raise Conflict("purchasing.invalid_transition", detail="The bill is already void")
    if row.amount_paid_minor > 0:
        raise Conflict("purchasing.bill_has_payments", detail="Void the bill's payments first")
    row.status = "void"
    row.voided_at = clock.now()
    row.version += 1
    _record(db, ctx, row, "voided", reason=data.reason)
    return row


def record_payment(db: Session, ctx: Context, bill_id: uuid.UUID, data: BillPaymentIn) -> SupplierBill:
    ctx.require(P.BILL_PAY)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, data.version)
    if row.status not in ("open", "partially_paid"):
        raise Conflict("purchasing.invalid_transition", detail=f"Cannot pay a {row.status} bill")
    if data.paid_on > business_today(db, ctx):
        raise Invalid("purchasing.payment_date_in_future")
    if data.amount_minor > balance(row):
        raise Conflict(
            "purchasing.overpayment", detail="The payment is larger than the open balance", balance_minor=balance(row)
        )
    payment = SupplierBillPayment(
        business_id=ctx.business_id,
        bill_id=row.id,
        amount_minor=data.amount_minor,
        paid_on=data.paid_on,
        method=data.method,
        reference=data.reference,
    )
    db.add(payment)
    row.amount_paid_minor += data.amount_minor
    row.status = _status_after_payments(row)
    row.version += 1
    db.flush()
    _record(db, ctx, row, "payment_recorded", payment_id=str(payment.id), amount_minor=data.amount_minor)
    return row


def void_payment(db: Session, ctx: Context, bill_id: uuid.UUID, payment_id: uuid.UUID, version: int) -> SupplierBill:
    ctx.require(P.BILL_VOID)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, version)
    payment = db.scalar(
        select(SupplierBillPayment)
        .where(
            SupplierBillPayment.business_id == ctx.business_id,
            SupplierBillPayment.bill_id == row.id,
            SupplierBillPayment.id == payment_id,
        )
        .with_for_update()
    )
    if payment is None:
        raise NotFound("purchasing.bill_payment_not_found")
    if payment.voided_at is not None:
        raise Conflict("purchasing.bill_payment_already_voided")
    payment.voided_at = clock.now()
    payment.version += 1
    row.amount_paid_minor -= payment.amount_minor
    row.status = _status_after_payments(row)
    row.version += 1
    _record(db, ctx, row, "payment_voided", payment_id=str(payment.id), amount_minor=payment.amount_minor)
    return row


def payments_of(db: Session, ctx: Context, bill_id: uuid.UUID) -> list[SupplierBillPayment]:
    return list(
        db.scalars(
            select(SupplierBillPayment)
            .where(SupplierBillPayment.business_id == ctx.business_id, SupplierBillPayment.bill_id == bill_id)
            .order_by(SupplierBillPayment.paid_on, SupplierBillPayment.created_at, SupplierBillPayment.id)
        )
    )


def _po_numbers(db: Session, ctx: Context, ids: set[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    if not ids:
        return {}
    rows = db.execute(
        select(PurchaseOrder.id, PurchaseOrder.number).where(
            PurchaseOrder.business_id == ctx.business_id, PurchaseOrder.id.in_(ids)
        )
    ).all()
    return {row.id: row.number for row in rows}


def _out(row: SupplierBill, po_number: str | None, payments: list[SupplierBillPayment] | None) -> BillOut:
    out = BillOut.model_validate(row)
    out.balance_minor = balance(row)
    out.purchase_order_number = po_number
    if payments is not None:
        out.payments = [BillPaymentOut.model_validate(p) for p in payments]
    return out


def detail(db: Session, ctx: Context, row: SupplierBill) -> BillOut:
    numbers = _po_numbers(db, ctx, {row.purchase_order_id} if row.purchase_order_id else set())
    po_number = numbers.get(row.purchase_order_id) if row.purchase_order_id else None
    return _out(row, po_number, payments_of(db, ctx, row.id))


def listing(
    db: Session,
    ctx: Context,
    limit: int,
    cursor: str | None,
    *,
    statuses: list[BillStatus] | None = None,
    supplier_id: uuid.UUID | None = None,
    purchase_order_id: uuid.UUID | None = None,
) -> tuple[list[BillOut], str | None]:
    ctx.require(P.BILL_READ)
    stmt: Select[SupplierBill] = select(SupplierBill).where(SupplierBill.business_id == ctx.business_id)
    if statuses:
        stmt = stmt.where(SupplierBill.status.in_(statuses))
    if supplier_id is not None:
        stmt = stmt.where(SupplierBill.supplier_id == supplier_id)
    if purchase_order_id is not None:
        stmt = stmt.where(SupplierBill.purchase_order_id == purchase_order_id)
    rows, next_cursor = crud.page(db, stmt, SupplierBill, limit, cursor)
    numbers = _po_numbers(db, ctx, {r.purchase_order_id for r in rows if r.purchase_order_id})
    items = [_out(r, numbers.get(r.purchase_order_id) if r.purchase_order_id else None, None) for r in rows]
    return items, next_cursor


def po_billing(db: Session, ctx: Context, po_id: uuid.UUID) -> PurchaseOrderBilling:
    ctx.require(P.BILL_READ)
    po = repo.order(db, ctx, po_id)
    received = sum(
        int((line.received_quantity * line.unit_cost_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        for line in repo.lines(db, ctx, po.id)
    )
    billed = db.scalar(
        select(func.coalesce(func.sum(SupplierBill.total_minor), 0)).where(
            SupplierBill.business_id == ctx.business_id,
            SupplierBill.purchase_order_id == po.id,
            SupplierBill.status != "void",
        )
    )
    billed_minor = int(billed or 0)
    return PurchaseOrderBilling(
        purchase_order_id=po.id,
        currency=po.currency,
        ordered_minor=po.total_minor,
        received_minor=received,
        billed_minor=billed_minor,
        unbilled_received_minor=max(0, received - billed_minor),
    )
