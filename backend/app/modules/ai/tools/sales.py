"""Sales tools (PR-01 functions; contracts §10, integration-map §B/§D): list_overdue_invoices (read) and
propose_payment_reminder (AI-07).

The DTO's `bill_to_email` is dropped: addresses stay server-side; the payment reminder executor looks them up itself.
The reminder uses a locked point read; background drafts carry the expected version server-side.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.sales import public as sales
from app.platform import clock
from app.platform.context import Context
from app.platform.errors import Conflict, NotFound
from app.platform.money import money_text

from .base import RunState, Tool
from .common import MAX_LIST_ITEMS, NoArgs, ToolArgs, cite, day, listing, money, utc
from .proposing import PlainText, gather, propose, tool_result

READ = "sales.document.read"
PROPOSE = "ai.assistant.use"


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
    invoice = sales.get_overdue_invoice(db, ctx, args.invoice_id, lock=True)
    if invoice is None:
        raise NotFound("ai.overdue_invoice_not_found", detail="not an overdue invoice of this business")
    expected = state.server_facts.get("invoice_version")
    if expected is not None and invoice.version != expected:
        raise Conflict("ai.invoice_changed")
    if invoice.last_reminder_at is not None and invoice.last_reminder_at > clock.now() - timedelta(days=7):
        raise Conflict("ai.reminder_cooldown")
    if approvals.find_open_proposals(db, ctx, action_types=["sales.send_payment_reminder"], invoice_id=invoice.id):
        raise Conflict("ai.reminder_pending")
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
