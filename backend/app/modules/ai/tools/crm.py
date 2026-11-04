"""CRM read tools: find_customers, get_customer, customer_history (contracts §10, integration-map §B).

No raw contact value ever leaves these tools: summaries carry `contact_types` only, and timeline text is redacted.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.modules.crm import public as crm
from app.platform.context import Context

from .base import RunState, Tool
from .common import ToolArgs, cite, listing, redact_contacts, utc

READ = "crm.customer.read"


class FindCustomersArgs(ToolArgs):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    phone: str | None = Field(default=None, min_length=3, max_length=40)

    @model_validator(mode="after")
    def one_criterion(self) -> FindCustomersArgs:
        if not (self.name or self.email or self.phone):
            raise ValueError("give a name, an email or a phone")
        return self


class CustomerArgs(ToolArgs):
    customer_id: uuid.UUID


class CustomerHistoryArgs(ToolArgs):
    customer_id: uuid.UUID
    limit: int = Field(default=20, ge=1, le=20)


def customer_view(summary: crm.CustomerSummary) -> dict[str, Any]:
    return {
        "customer_id": summary.id,
        "display_name": summary.display_name,
        "kind": summary.kind,
        "status": summary.status,
        "contact_types": sorted(set(summary.contact_types)),
        "tags": summary.tags,
        "timezone": summary.timezone,
        "last_interaction_at": utc(summary.last_interaction_at),
    }


def _find_customers(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, FindCustomersArgs)
    rows = crm.find_customer_candidates(db, ctx, name=args.name, email=args.email, phone=args.phone, limit=5)
    data = listing(
        "candidates",
        [
            {"customer_id": c.customer_id, "display_name": c.display_name, "score": round(c.score, 3),
             "reason": c.reason}
            for c in rows
        ],
    )  # fmt: skip
    cite(state, "find_customers", None, data)
    return data


def _get_customer(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, CustomerArgs)
    data = customer_view(crm.get_customer_summary(db, ctx, args.customer_id))
    cite(state, "get_customer", args.customer_id, data)
    return data


def _customer_history(db: Session, ctx: Context, args: BaseModel, state: RunState) -> dict[str, Any]:
    assert isinstance(args, CustomerHistoryArgs)
    entries = crm.get_timeline(db, ctx, args.customer_id, limit=args.limit)
    data = {
        "customer_id": args.customer_id,
        **listing(
            "entries",
            [
                {"occurred_at": utc(e.occurred_at), "kind": e.kind, "summary": redact_contacts(e.summary, 300),
                 "ref_type": e.ref_type, "ref_id": e.ref_id}
                for e in entries
            ],
            args.limit,
        ),
    }  # fmt: skip
    cite(state, "customer_history", args.customer_id, data)
    return data


TOOLS = (
    Tool(
        name="find_customers",
        description="Find existing customers by name, email or phone. Returns candidates with a match score (0-1) "
        "and the reason; never contact details.",
        kind="read", permission=READ, args_model=FindCustomersArgs, run=_find_customers,
    ),
    Tool(
        name="get_customer",
        description="One customer's summary: name, kind, status, which contact types exist, tags.",
        kind="read", permission=READ, args_model=CustomerArgs, run=_get_customer,
    ),
    Tool(
        name="customer_history",
        description="A customer's recent timeline (bookings, messages, documents), newest first, at most 20 entries.",
        kind="read", permission=READ, args_model=CustomerHistoryArgs, run=_customer_history,
    ),
)  # fmt: skip
