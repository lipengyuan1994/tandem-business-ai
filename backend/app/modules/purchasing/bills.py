"""Supplier bills (accounts payable), their lines, the payments made against them and supplier credits.

`supplier_bills.amount_paid_minor` always equals the sum of the bill's non-voided payments and `amount_credited_minor`
the sum of its live supplier credits; `amount_refunded_minor` is the part of those credits the supplier paid back.
The balance is total - (paid - refunded) - credited, and the status follows what is settled: open → partially_paid →
paid. Payments never exceed the balance. A credit takes off the balance first, and whatever goes beyond it must have
been refunded by the supplier. A bill with no live payments or credits can be voided; a payment or credit recorded in
error is voided, never deleted, and reopens the bill.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
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
from app.platform.refunds import refunds_covered

from . import permissions as P
from . import repository as repo
from .models import (
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierBill,
    SupplierBillLine,
    SupplierBillPayment,
    SupplierCredit,
)
from .schemas_bills import (
    BillIn,
    BillLineIn,
    BillLineOut,
    BillOut,
    BillPaymentIn,
    BillPaymentOut,
    BillStatus,
    BillUpdate,
    BillVoidIn,
    PurchaseOrderBilling,
    PurchaseOrderUnbilledLine,
    SupplierCreditIn,
    SupplierCreditOut,
)

BILLABLE_PO_STATUSES = ("approved", "sent", "partially_received", "received")


def business_today(db: Session, ctx: Context) -> date:
    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def get(db: Session, ctx: Context, bill_id: uuid.UUID, *, lock: bool = False) -> SupplierBill:
    return crud.require_scoped(db, ctx, SupplierBill, bill_id, "purchasing.bill_not_found", for_update=lock)


def balance(row: SupplierBill) -> int:
    if row.status == "void":
        return 0
    return row.total_minor - (row.amount_paid_minor - row.amount_refunded_minor) - row.amount_credited_minor


def _status_after_payments(row: SupplierBill) -> str:
    settled = row.amount_paid_minor - row.amount_refunded_minor + row.amount_credited_minor
    if settled == 0:
        return "open"
    return "paid" if settled == row.total_minor else "partially_paid"


def line_amount(quantity: Decimal, unit_cost_minor: int) -> int:
    return int((quantity * unit_cost_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _line_rows(db: Session, ctx: Context, row: SupplierBill, lines: list[BillLineIn]) -> list[SupplierBillLine]:
    """Checks each line's links (catalog item, a line of the bill's own order) and works out its amount."""
    items = {line.catalog_item_id for line in lines if line.catalog_item_id is not None}
    if items - set(catalog.get_items(db, ctx, items)):
        raise Invalid("purchasing.bill_line_item_unknown", detail="A line names an item that is not in the catalog")
    po_lines = {line.purchase_order_line_id for line in lines if line.purchase_order_line_id is not None}
    if po_lines:
        found = set(
            db.scalars(
                select(PurchaseOrderLine.id).where(
                    PurchaseOrderLine.business_id == ctx.business_id,
                    PurchaseOrderLine.id.in_(po_lines),
                    PurchaseOrderLine.purchase_order_id == row.purchase_order_id,
                )
            )
        )
        if row.purchase_order_id is None or po_lines - found:
            raise Invalid("purchasing.bill_line_not_on_order", detail="A line is not on this bill's purchase order")
    return [
        SupplierBillLine(
            business_id=ctx.business_id,
            bill_id=row.id,
            position=position,
            description=line.description,
            quantity=line.quantity,
            unit_cost_minor=line.unit_cost_minor,
            amount_minor=line_amount(line.quantity, line.unit_cost_minor),
            catalog_item_id=line.catalog_item_id,
            purchase_order_line_id=line.purchase_order_line_id,
        )
        for position, line in enumerate(lines)
    ]


def _lines_total(rows: list[SupplierBillLine], stated: int | None) -> int:
    total = sum(r.amount_minor for r in rows)
    if total <= 0 or total > 10**14:
        raise Invalid("purchasing.bill_lines_total", detail="The lines must add up to more than zero")
    if stated is not None and stated != total:
        raise Invalid("purchasing.bill_total_mismatch", detail="The total does not match the sum of the lines")
    return total


def lines_of(db: Session, ctx: Context, bill_id: uuid.UUID) -> list[SupplierBillLine]:
    return list(
        db.scalars(
            select(SupplierBillLine)
            .where(SupplierBillLine.business_id == ctx.business_id, SupplierBillLine.bill_id == bill_id)
            .order_by(SupplierBillLine.position)
        )
    )


def _replace_lines(db: Session, ctx: Context, row: SupplierBill, new: list[SupplierBillLine]) -> None:
    for old in lines_of(db, ctx, row.id):
        db.delete(old)
    db.flush()  # positions are unique per bill
    db.add_all(new)


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
    # Serialize bill entry per supplier, including the recheck for receipt-based bills.
    repo.supplier(db, ctx, supplier.id, lock=True)
    if data.purchase_order_id is not None:
        currency = _purchase_order(db, ctx, supplier.id, data.purchase_order_id).currency
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
        total_minor=data.total_minor or 0,
        amount_paid_minor=0,
        status="open",
        notes=data.notes,
        created_by=ctx.user_id,
    )
    lines: list[SupplierBillLine] = []
    if data.lines is not None:
        lines = _line_rows(db, ctx, row, data.lines)
        row.total_minor = _lines_total(lines, data.total_minor)
    if data.match_received:
        assert data.purchase_order_id is not None
        remaining = _remaining_received(db, ctx, data.purchase_order_id)
        requested: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
        for line in lines:
            if line.purchase_order_line_id is not None:
                requested[line.purchase_order_line_id] += line.quantity
        if not requested or remaining is None or any(qty > remaining.get(key, 0) for key, qty in requested.items()):
            raise Conflict(
                "purchasing.bill_receipts_changed",
                detail="Received and unbilled quantities changed; refill or enter the bill manually",
            )
    db.add(row)
    db.flush()
    for line in lines:
        line.bill_id = row.id
    db.add_all(lines)
    _record(db, ctx, row, "created", total_minor=row.total_minor, lines=len(lines))
    return row


def update(db: Session, ctx: Context, bill_id: uuid.UUID, data: BillUpdate) -> SupplierBill:
    ctx.require(P.BILL_WRITE)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, data.version)
    if row.status != "open" or row.amount_credited_minor > 0:
        raise Conflict("purchasing.bill_locked", detail="Only open bills with no payments or credits can be edited")
    fields = data.model_fields_set - {"version"}
    if "bill_date" in fields and data.bill_date is not None:
        _check_bill_date(db, ctx, data.bill_date)
        row.bill_date = data.bill_date
    if "due_date" in fields:
        row.due_date = data.due_date
    if row.due_date is not None and row.due_date < row.bill_date:
        raise Invalid("purchasing.bill_due_before_bill_date")
    if fields & {"supplier_reference", "lines", "total_minor"}:
        repo.supplier(db, ctx, row.supplier_id, lock=True)
    if "supplier_reference" in fields:
        _check_duplicate(db, ctx, row.supplier_id, data.supplier_reference, exclude=row.id)
        row.supplier_reference = data.supplier_reference
    if "lines" in fields and data.lines is not None:
        lines = _line_rows(db, ctx, row, data.lines)
        row.total_minor = _lines_total(lines, data.total_minor if "total_minor" in fields else None)
        _replace_lines(db, ctx, row, lines)
    else:
        if "lines" in fields:  # lines: null drops the itemisation
            _replace_lines(db, ctx, row, [])
        elif "total_minor" in fields and lines_of(db, ctx, row.id):
            raise Invalid("purchasing.bill_total_from_lines", detail="This bill's total is the sum of its lines")
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
    if row.amount_credited_minor > 0:
        raise Conflict("purchasing.bill_has_credits", detail="Void the bill's supplier credits first")
    repo.supplier(db, ctx, row.supplier_id, lock=True)
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


def _refund_dates_covered(
    db: Session,
    ctx: Context,
    bill_id: uuid.UUID,
    *,
    extra: tuple[date, int] | None = None,
    exclude_payment: uuid.UUID | None = None,
) -> bool:
    paid = (
        (p.paid_on, p.amount_minor)
        for p in payments_of(db, ctx, bill_id)
        if p.voided_at is None and p.id != exclude_payment
    )
    refunded = [
        (c.refund_received_on, c.refund_minor)
        for c in credits_of(db, ctx, bill_id)
        if c.voided_at is None and c.refund_received_on is not None
    ]
    if extra is not None:
        refunded.append(extra)
    return refunds_covered(paid, refunded)


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
    if row.amount_refunded_minor and not _refund_dates_covered(db, ctx, row.id, exclude_payment=payment.id):
        raise Conflict(
            "purchasing.payment_refunded",
            detail="The supplier refunded part of this payment with a credit; void that credit first",
        )
    payment.voided_at = clock.now()
    payment.version += 1
    row.amount_paid_minor -= payment.amount_minor
    row.status = _status_after_payments(row)
    row.version += 1
    _record(db, ctx, row, "payment_voided", payment_id=str(payment.id), amount_minor=payment.amount_minor)
    return row


def add_credit(db: Session, ctx: Context, bill_id: uuid.UUID, data: SupplierCreditIn) -> SupplierBill:
    """Records a supplier credit. It takes off the balance first; anything beyond that the supplier must have paid back,
    recorded as the credit's refund."""
    ctx.require(P.BILL_WRITE)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, data.version)
    if row.status not in ("open", "partially_paid", "paid"):
        raise Conflict("purchasing.invalid_transition", detail=f"Cannot credit a {row.status} bill")
    today = business_today(db, ctx)
    if data.credit_date > today:
        raise Invalid("purchasing.credit_date_in_future")
    creditable = row.total_minor - row.amount_credited_minor
    if data.amount_minor > creditable:
        raise Conflict(
            "purchasing.credit_exceeds_bill",
            detail="The credit is larger than what is left to credit on the bill",
            creditable_minor=creditable,
        )
    refund = max(0, data.amount_minor - balance(row))
    if refund > 0 and data.refund is None:
        raise Conflict(
            "purchasing.refund_required",
            detail="The credit is more than the bill still owes; record how the supplier paid back the difference",
            refund_minor=refund,
        )
    if refund == 0 and data.refund is not None:
        raise Invalid("purchasing.refund_not_needed", detail="The bill still owes more than this credit")
    if data.refund is not None and data.refund.received_on > today:
        raise Invalid("purchasing.payment_date_in_future")
    if data.refund is not None and not _refund_dates_covered(db, ctx, bill_id, extra=(data.refund.received_on, refund)):
        raise Conflict(
            "purchasing.refund_exceeds_dated_payments",
            detail="Recorded payments do not cover all refunds on their effective dates",
        )
    credit = SupplierCredit(
        business_id=ctx.business_id,
        bill_id=row.id,
        credit_date=data.credit_date,
        amount_minor=data.amount_minor,
        supplier_reference=data.supplier_reference,
        reason=data.reason,
        refund_minor=refund,
        refund_received_on=data.refund.received_on if data.refund else None,
        refund_method=data.refund.method if data.refund else None,
        refund_reference=data.refund.reference if data.refund else None,
    )
    db.add(credit)
    row.amount_credited_minor += data.amount_minor
    row.amount_refunded_minor += refund
    row.status = _status_after_payments(row)
    row.version += 1
    db.flush()
    _record(
        db, ctx, row, "credit_recorded", credit_id=str(credit.id), amount_minor=data.amount_minor, refund_minor=refund
    )
    return row


def void_credit(db: Session, ctx: Context, bill_id: uuid.UUID, credit_id: uuid.UUID, version: int) -> SupplierBill:
    ctx.require(P.BILL_VOID)
    row = get(db, ctx, bill_id, lock=True)
    check_version(row, version)
    credit = db.scalar(
        select(SupplierCredit)
        .where(
            SupplierCredit.business_id == ctx.business_id,
            SupplierCredit.bill_id == row.id,
            SupplierCredit.id == credit_id,
        )
        .with_for_update()
    )
    if credit is None:
        raise NotFound("purchasing.supplier_credit_not_found")
    if credit.voided_at is not None:
        raise Conflict("purchasing.supplier_credit_already_voided")
    credit.voided_at = clock.now()
    credit.version += 1
    row.amount_credited_minor -= credit.amount_minor
    row.amount_refunded_minor -= credit.refund_minor
    row.status = _status_after_payments(row)
    row.version += 1
    _record(db, ctx, row, "credit_voided", credit_id=str(credit.id), amount_minor=credit.amount_minor)
    return row


def credits_of(db: Session, ctx: Context, bill_id: uuid.UUID) -> list[SupplierCredit]:
    return list(
        db.scalars(
            select(SupplierCredit)
            .where(SupplierCredit.business_id == ctx.business_id, SupplierCredit.bill_id == bill_id)
            .order_by(SupplierCredit.credit_date, SupplierCredit.created_at, SupplierCredit.id)
        )
    )


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
    out = _out(row, po_number, payments_of(db, ctx, row.id))
    out.lines = [BillLineOut.model_validate(line) for line in lines_of(db, ctx, row.id)]
    out.credits = [SupplierCreditOut.model_validate(c) for c in credits_of(db, ctx, row.id)]
    return out


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


def _remaining_received(db: Session, ctx: Context, po_id: uuid.UUID) -> dict[uuid.UUID, Decimal] | None:
    received = {line.id: line.received_quantity for line in repo.lines(db, ctx, po_id)}
    billed: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    # Credits are amounts, not quantities. Don't assume they free previously invoiced units.
    rows = db.execute(
        select(SupplierBillLine.purchase_order_line_id, SupplierBillLine.quantity)
        .select_from(SupplierBill)
        .outerjoin(
            SupplierBillLine,
            (SupplierBillLine.business_id == SupplierBill.business_id) & (SupplierBillLine.bill_id == SupplierBill.id),
        )
        .where(
            SupplierBill.business_id == ctx.business_id,
            SupplierBill.purchase_order_id == po_id,
            SupplierBill.status != "void",
        )
    )
    for line_id, quantity in rows:
        if line_id not in received or quantity is None:
            return None  # total-only or unallocated bill lines: require manual allocation
        billed[line_id] += quantity
    return {key: qty - billed[key] for key, qty in received.items() if qty > billed[key]}


def po_billing(db: Session, ctx: Context, po_id: uuid.UUID) -> PurchaseOrderBilling:
    ctx.require(P.BILL_READ)
    po = repo.order(db, ctx, po_id)
    received = sum(
        int((line.received_quantity * line.unit_cost_minor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        for line in repo.lines(db, ctx, po.id)
    )
    # What the supplier credited back is no longer billed.
    billed = db.scalar(
        select(func.coalesce(func.sum(SupplierBill.total_minor - SupplierBill.amount_credited_minor), 0)).where(
            SupplierBill.business_id == ctx.business_id,
            SupplierBill.purchase_order_id == po.id,
            SupplierBill.status != "void",
        )
    )
    billed_minor = int(billed or 0)
    remaining = _remaining_received(db, ctx, po.id)
    return PurchaseOrderBilling(
        purchase_order_id=po.id,
        currency=po.currency,
        ordered_minor=po.total_minor,
        received_minor=received,
        billed_minor=billed_minor,
        unbilled_received_minor=max(0, received - billed_minor),
        remaining_received_lines=None
        if remaining is None
        else [PurchaseOrderUnbilledLine(purchase_order_line_id=key, quantity=qty) for key, qty in remaining.items()],
    )
