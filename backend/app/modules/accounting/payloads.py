"""Pure builders: Tandem ledger records -> QuickBooks Online request bodies. No I/O here.

Amounts go out as decimals in the document currency. Every body is hashed (without Id/SyncToken) so an unchanged
record is never sent twice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.modules.payments.public import LedgerPayout
from app.modules.purchasing.public import LedgerBill, LedgerBillPayment
from app.modules.sales.public import LedgerInvoice, LedgerPayment
from app.platform.money import currency_exponent

DOC_NUMBER_MAX = 21  # QuickBooks DocNumber / PaymentRefNum limit
NAME_MAX = 100  # Customer / Vendor DisplayName limit
SALES_ITEM_NAME = "Tandem sales"
TAX_ITEM_NAME = "Tandem sales tax"


@dataclass(frozen=True, slots=True)
class SalesRefs:
    customer_id: str
    sales_item_id: str
    tax_item_id: str | None
    receivable_account_id: str | None
    us_company: bool


def amount(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-currency_exponent(currency))


def ref(value: str) -> dict[str, str]:
    return {"value": value}


def clean_name(name: str) -> str:
    """QuickBooks display names may not contain ':' (it separates sub-customers) or line breaks."""
    text = " ".join(name.replace(":", "-").split())
    return text[:NAME_MAX] or "Unnamed"


def doc_number(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    # QuickBooks caps DocNumber; keep the end, where numbers differ.
    return text[-DOC_NUMBER_MAX:] or None


def content_hash(body: dict[str, Any]) -> str:
    stable = {k: v for k, v in body.items() if k not in ("Id", "SyncToken", "sparse")}
    raw = json.dumps(stable, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def party(name: str, email: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"DisplayName": clean_name(name)}
    if email:
        body["PrimaryEmailAddr"] = {"Address": email}
    return body


def service_item(name: str, income_account_id: str) -> dict[str, Any]:
    return {"Name": name, "Type": "Service", "IncomeAccountRef": ref(income_account_id)}


def _sales_line(description: str, total: Decimal, quantity: Decimal, unit: Decimal, refs: SalesRefs) -> dict[str, Any]:
    detail: dict[str, Any] = {"ItemRef": ref(refs.sales_item_id), "Qty": quantity, "UnitPrice": unit}
    if refs.us_company:
        detail["TaxCodeRef"] = ref("NON")  # Tandem already charged the tax; it travels as its own line
    return {
        "DetailType": "SalesItemLineDetail",
        "Amount": total,
        "Description": description[:4000],
        "SalesItemLineDetail": detail,
    }


def sales_document(doc: LedgerInvoice, refs: SalesRefs) -> dict[str, Any]:
    """Invoice or CreditMemo body. Tandem's sales tax becomes one line on the 'Tandem sales tax' item."""
    cur = doc.currency
    lines: list[dict[str, Any]] = []
    line_total = 0
    for line in doc.lines:
        total = amount(line.subtotal_minor, cur)
        unit = amount(line.unit_price_minor, cur)
        if line.quantity * unit == total:
            lines.append(_sales_line(line.name, total, line.quantity, unit, refs))
        else:  # discounts or rounding: keep the exact amount, show the quantity in the text
            lines.append(_sales_line(f"{line.name} (x {line.quantity.normalize()})", total, Decimal(1), total, refs))
        line_total += line.subtotal_minor
    rest = doc.total_minor - doc.tax_minor - line_total
    if rest:
        value = amount(rest, cur)
        lines.append(_sales_line("Adjustment", value, Decimal(1), value, refs))
    if doc.tax_minor:
        if refs.tax_item_id is None:
            raise ValueError("tax item required")
        tax = amount(doc.tax_minor, cur)
        tax_line = _sales_line("Sales tax", tax, Decimal(1), tax, refs)
        tax_line["SalesItemLineDetail"]["ItemRef"] = ref(refs.tax_item_id)
        lines.append(tax_line)
    body: dict[str, Any] = {
        "CustomerRef": ref(refs.customer_id),
        "TxnDate": doc.issue_date.isoformat(),
        "Line": lines,
        "PrivateNote": f"From Tandem {doc.number}",
    }
    if number := doc_number(doc.number):
        body["DocNumber"] = number
    if doc.kind == "invoice":
        if doc.due_date:
            body["DueDate"] = doc.due_date.isoformat()
        if doc.bill_to_email:
            body["BillEmail"] = {"Address": doc.bill_to_email}
        if refs.receivable_account_id:
            body["ARAccountRef"] = ref(refs.receivable_account_id)
    return body


def credit_application(
    customer_id: str, invoice_id: str, credit_memo_id: str, total_minor: int, currency: str, when: str
) -> dict[str, Any]:
    """A zero-amount Payment that applies a credit memo to the invoice it cancels."""
    value = amount(total_minor, currency)
    return {
        "CustomerRef": ref(customer_id),
        "TotalAmt": Decimal(0),
        "TxnDate": when,
        "Line": [
            {"Amount": value, "LinkedTxn": [{"TxnId": invoice_id, "TxnType": "Invoice"}]},
            {"Amount": value, "LinkedTxn": [{"TxnId": credit_memo_id, "TxnType": "CreditMemo"}]},
        ],
        "PrivateNote": "Tandem credit note applied to its invoice",
    }


def customer_payment(
    payment: LedgerPayment, customer_id: str, invoice_id: str, deposit_account_id: str | None
) -> dict[str, Any]:
    """`deposit_account_id` is the Stripe clearing account for money taken through Stripe (when one is mapped)."""
    value = amount(payment.amount_minor, payment.currency)
    body: dict[str, Any] = {
        "CustomerRef": ref(customer_id),
        "TotalAmt": value,
        "TxnDate": payment.paid_on.isoformat(),
        "Line": [{"Amount": value, "LinkedTxn": [{"TxnId": invoice_id, "TxnType": "Invoice"}]}],
        "PrivateNote": f"Tandem payment ({payment.method.replace('_', ' ')})",
    }
    if number := doc_number(payment.reference):
        body["PaymentRefNum"] = number
    if deposit_account_id:
        body["DepositToAccountRef"] = ref(deposit_account_id)
    return body


def bill(row: LedgerBill, vendor_id: str, expense_account_id: str, payable_account_id: str | None) -> dict[str, Any]:
    value = amount(row.total_minor, row.currency)
    note = f"From Tandem, purchase order {row.purchase_order_number}" if row.purchase_order_number else "From Tandem"
    body: dict[str, Any] = {
        "VendorRef": ref(vendor_id),
        "TxnDate": row.bill_date.isoformat(),
        "Line": [
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": amount(line.amount_minor, row.currency),
                "Description": line.description,
                "AccountBasedExpenseLineDetail": {"AccountRef": ref(expense_account_id)},
            }
            for line in row.lines
            if line.amount_minor > 0
        ]
        or [
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": value,
                "Description": f"Bill {row.supplier_reference}" if row.supplier_reference else "Supplier bill",
                "AccountBasedExpenseLineDetail": {"AccountRef": ref(expense_account_id)},
            }
        ],
        "PrivateNote": note,
    }
    if number := doc_number(row.supplier_reference):
        body["DocNumber"] = number
    if row.due_date:
        body["DueDate"] = row.due_date.isoformat()
    if payable_account_id:
        body["APAccountRef"] = ref(payable_account_id)
    return body


def bill_payment(row: LedgerBillPayment, vendor_id: str, bill_id: str, bank_account_id: str) -> dict[str, Any]:
    value = amount(row.amount_minor, row.currency)
    body: dict[str, Any] = {
        "VendorRef": ref(vendor_id),
        "PayType": "Check",
        "CheckPayment": {"BankAccountRef": ref(bank_account_id)},
        "TotalAmt": value,
        "TxnDate": row.paid_on.isoformat(),
        "Line": [{"Amount": value, "LinkedTxn": [{"TxnId": bill_id, "TxnType": "Bill"}]}],
        "PrivateNote": f"Tandem bill payment ({row.method.replace('_', ' ')})",
    }
    if number := doc_number(row.reference):
        body["DocNumber"] = number
    return body


def payout_transfer(payout: LedgerPayout, clearing_account_id: str, bank_account_id: str) -> dict[str, Any]:
    """Stripe paying out: the money moves from the Stripe clearing account to the bank account."""
    assert payout.arrival_on is not None
    return {
        "FromAccountRef": ref(clearing_account_id),
        "ToAccountRef": ref(bank_account_id),
        "Amount": amount(payout.amount_minor, payout.currency),
        "TxnDate": payout.arrival_on.isoformat(),
        "PrivateNote": f"Stripe payout {payout.stripe_payout_id} (from Tandem)",
    }


def payout_fees(payout: LedgerPayout, clearing_account_id: str, fees_account_id: str) -> dict[str, Any]:
    """Stripe's fees on what one payout contained, paid out of the Stripe clearing account."""
    assert payout.arrival_on is not None
    value = amount(payout.fees_minor, payout.currency)
    return {
        "PaymentType": "Cash",
        "AccountRef": ref(clearing_account_id),
        "TxnDate": payout.arrival_on.isoformat(),
        "TotalAmt": value,
        "Line": [
            {
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": value,
                "Description": "Stripe processing fees",
                "AccountBasedExpenseLineDetail": {"AccountRef": ref(fees_account_id)},
            }
        ],
        "PrivateNote": f"Stripe fees in payout {payout.stripe_payout_id} (from Tandem)",
    }
