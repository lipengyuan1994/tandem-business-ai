"""Sales tools (PR-01 functions; contracts §10, integration-map §B/§D): list_overdue_invoices (read) and
propose_payment_reminder (AI-07).

The DTO's `bill_to_email` is dropped: addresses stay server-side; the payment reminder executor looks them up itself.
The reminder's invoice version comes from the overdue list, never from the model.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.modules.sales import public as sales
from app.platform.context import Context
from app.platform.errors import NotFound
from app.platform.money import money_text

from .base import RunState, Tool
from .common import MAX_LIST_ITEMS, NoArgs, ToolArgs, cite, day, listing, money, utc
from .proposing import PlainText, gather, propose, tool_result

READ = "sales.document.read"
PROPOSE = "ai.assistant.use"
OVERDUE_LOOKUP_LIMIT = 500


def _list_overdue_invoices(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    rows = sales.list_overdue_invoices(db, ctx, limit=MAX_LIST_ITEMS + 1)
    data = listing(
        "invoices",
        [
            {"invoice_id": i.id, "invoice_version": i.version, "number": i.number, "customer_id": i.customer_id,
             "bill_to_name": i.bill_to_name, "due_date": day(i.due_date), "days_overdue": i.days_overdue,
             "total": money(i.total_minor, i.currency), "last_reminder_at": utc(i.last_reminder_at)}
            for i in rows
        ],
    )  # fmt: skip
    cite(state, "list_overdue_invoices", None, data)
    return data


class ProposePaymentReminderArgs(ToolArgs):
    effect_fields: ClassVar[tuple[str, ...]] = ("invoice_id",)
    invoice_id: uuid.UUID
    text: PlainText = Field(max_length=2000)


def _propose_payment_reminder(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, ProposePaymentReminderArgs)
    overdue = sales.list_overdue_invoices(db, ctx, limit=OVERDUE_LOOKUP_LIMIT)
    invoice = next((i for i in overdue if i.id == args.invoice_id), None)
    if invoice is None:
        raise NotFound("ai.overdue_invoice_not_found", detail="not an overdue invoice of this business")
    payload = {"invoice_id": str(invoice.id), "invoice_version": invoice.version, "body": args.text}
    amount = money_text(invoice.total_minor, invoice.currency)
    title = f"Payment reminder: {invoice.number}, {invoice.bill_to_name}, {amount}, {invoice.days_overdue} days overdue"
    facts = {"number": invoice.number, "bill_to_name": invoice.bill_to_name, "total": amount,
             "due_date": day(invoice.due_date), "days_overdue": invoice.days_overdue,
             "last_reminder_at": utc(invoice.last_reminder_at)}  # fmt: skip
    own = [tool_result("propose_payment_reminder", invoice.id, facts)]
    evidence = gather(db, ctx, state, own, [invoice.id, invoice.customer_id])
    return propose(db, ctx, action_type="sales.send_payment_reminder", payload=payload, title=title,
                   evidence=evidence)  # fmt: skip


TOOLS = (
    Tool(
        name="list_overdue_invoices",
        description="Issued invoices past their due date: number, customer, days overdue, total, last reminder. "
        "No email addresses.",
        kind="read", permission=READ, args_model=NoArgs, run=_list_overdue_invoices,
    ),
    Tool(
        name="propose_payment_reminder",
        description="Propose a plain-text payment reminder for an overdue invoice (ids from list_overdue_invoices). "
        "Amounts and dates in the text must match the invoice.",
        kind="propose", permission=PROPOSE, args_model=ProposePaymentReminderArgs, run=_propose_payment_reminder,
    ),
)  # fmt: skip
