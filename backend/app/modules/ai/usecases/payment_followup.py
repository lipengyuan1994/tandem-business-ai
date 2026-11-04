"""U10: draft overdue reminders as proposals; delivery always requires human approval."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, timedelta
from functools import partial

from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.sales import public as sales
from app.platform import clock
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError
from app.platform.money import money_text

from ..agent.contexts import background_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router
from ..schemas import PaymentReminderDraft
from ..structured import complete
from ..tools.base import RunState
from ..tools.common import cite, redact_contacts
from ..tools.registry import execute_tool, get_tool

# The public overdue reader and the proposal tool both support at most 500 invoices.
_LIMIT = 500
_COOLDOWN = timedelta(days=7)
_RELATIVE_DATE = re.compile(
    r"\b(?:today|tomorrow|yesterday|tonight|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.I,
)
_NUMBER_WORD = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand|million|billion|dollars?|euros?|pounds?|yen)\b",
    re.I,
)


def _eligible(invoice: sales.OverdueInvoice) -> bool:
    return bool(invoice.bill_to_email and invoice.bill_to_email.strip()) and (
        invoice.last_reminder_at is None or invoice.last_reminder_at <= clock.now() - _COOLDOWN
    )


def _pending(db: Session, ctx: Context, invoice: sales.OverdueInvoice) -> bool:
    return bool(approvals.find_open_proposals(
        db, ctx, action_types=["sales.send_payment_reminder"], invoice_id=invoice.id,
    ))  # fmt: skip


def _validate(value: PaymentReminderDraft, invoice: sales.OverdueInvoice) -> None:
    get_tool("propose_payment_reminder").args_model.model_validate({"invoice_id": invoice.id, "text": value.text})
    # Require complete amount/currency and ISO date tokens, not individual numbers that could be recombined.
    # The model can omit amounts/dates; any included values must use these exact, unambiguous formats.
    rest = value.text
    allowed = [
        money_text(invoice.total_minor, invoice.currency),
        invoice.due_date.isoformat(),
        invoice.number,
        f"{invoice.days_overdue} days overdue",
    ]
    for token in sorted(allowed, key=len, reverse=True):
        rest = re.sub(r"(?<![\w.+-])" + re.escape(token) + r"(?!\w|[.,]\d)", "", rest)
    if re.search(r"\d|[$€£¥]", rest) or _RELATIVE_DATE.search(rest) or _NUMBER_WORD.search(rest):
        raise ValueError("Use only the exact invoice number, amount with ISO currency, ISO due date, or days overdue.")
    if re.search(r"\b(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|BHD|KWD)\b", rest):
        raise ValueError("Currency must occur only as part of the exact invoice amount.")


def run(business_id: uuid.UUID, *, router: Router | None = None) -> list[uuid.UUID]:
    factory = background_ctx_factory(business_id, use_case="payment_followup")
    read_ctx = factory(uuid.uuid4())
    with transaction(read_ctx) as db:
        invoices = sales.list_overdue_invoices(db, read_ctx, limit=_LIMIT)
        invoices = [i for i in invoices if _eligible(i) and not _pending(db, read_ctx, i)]
    proposed: list[uuid.UUID] = []
    for invoice in invoices:
        data = {
            "number": invoice.number,
            "bill_to_name": redact_contacts(invoice.bill_to_name),
            "total": money_text(invoice.total_minor, invoice.currency),
            "due_date": invoice.due_date.isoformat(),
            "days_overdue": invoice.days_overdue,
        }
        trigger_key = f"payment_followup:{invoice.id}:{clock.now().astimezone(UTC).date().isoformat()}"
        run_id, value = complete(
            factory, use_case="payment_followup", prompt=load_prompt("payment_reminder"),
            facts=[ContextBlock(label="invoice", source_id=invoice.id, text=json.dumps(data), untrusted=True)],
            output_model=PaymentReminderDraft, trigger_type="cron", trigger_key=trigger_key,
            router=router, validate=partial(_validate, invoice=invoice),
        )  # fmt: skip
        ctx = factory(run_id)
        with transaction(ctx) as db:
            current = next((i for i in sales.list_overdue_invoices(db, ctx, limit=_LIMIT) if i.id == invoice.id), None)
            if current is None or not _eligible(current) or _pending(db, ctx, current):
                continue
            # A new revision needs fresh drafting, even if the writer's text omitted the changed values.
            if current.version != invoice.version or current != invoice:
                continue
        state = RunState(run_id=run_id, trigger_key=trigger_key)
        cite(state, "list_overdue_invoices", invoice.id, data)
        result = execute_tool(ctx, "propose_payment_reminder", {"invoice_id": str(invoice.id), "text": value.text},
                              ["propose_payment_reminder"], state)  # fmt: skip
        if not result["ok"]:
            raise DomainError(result["error"], result.get("detail"))
        proposed.append(uuid.UUID(str(result["data"]["proposal_id"])))
    return proposed
