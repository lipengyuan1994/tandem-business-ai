"""Complaints are human work. Classification cannot decide liability or authorize a reply."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.sales import public as sales
from app.modules.scheduling import public as scheduling
from app.platform import jobs
from app.platform.context import Context, workflow_scope
from app.platform.db import transaction

from .. import recording
from ..agent.contexts import background_ctx_factory
from ..agent.prompts import load_prompt
from ..team_settings import enabled
from .models import CareCase
from .workflows import lock_subject, prepare


def customer_linked(db: Session, ctx: Context, conversation_id: uuid.UUID, customer_id: uuid.UUID) -> None:
    """Called with customer then conversation locks held, including when the agent is now disabled."""
    for case in db.scalars(
        select(CareCase)
        .where(
            CareCase.business_id == ctx.business_id,
            CareCase.conversation_id == conversation_id,
            CareCase.customer_id.is_(None),
            CareCase.status == "open",
        )
        .with_for_update()
    ):
        case.customer_id = customer_id
    db.flush()


def enqueue(db: Session, ctx: Context, message_id: uuid.UUID, category: str, priority: str = "high") -> None:
    if category not in {"complaint", "warranty", "dispute"} or not enabled(db, ctx, "customer_care", lock=True):
        return
    source = conversations.get_message_context(db, ctx, message_id)
    conversations.lock_for_customer(db, ctx, source.conversation_id)
    source = conversations.get_message_context(db, ctx, message_id)
    if source.author_kind not in {"visitor", "customer"}:
        return
    if source.customer_id:
        value = crm.customer_care_contact(db, ctx, source.customer_id)
        if value is None or value.status != "active":
            return
    lock_subject(db, ctx, f"case:{message_id}")
    if not db.scalar(
        select(CareCase.id).where(CareCase.business_id == ctx.business_id, CareCase.source_message_id == message_id)
    ):
        db.add(
            CareCase(
                business_id=ctx.business_id,
                customer_id=source.customer_id,
                conversation_id=source.conversation_id,
                source_message_id=message_id,
                category=category,
                priority=priority if priority in {"normal", "high", "urgent"} else "high",
                status="open",
            )
        )
        db.flush()
    jobs.enqueue(
        db,
        "ai.customer_care_triage",
        {
            "message_id": str(message_id),
            "category": category,
            "priority": priority if priority in {"normal", "high", "urgent"} else "high",
        },
        business_id=ctx.business_id,
        dedupe_key=f"care-triage:{ctx.business_id}:{message_id}",
        priority=10,
        ctx=ctx,
    )


def run(ctx: Context, payload: dict[str, Any]) -> None:
    assert ctx.business_id is not None
    factory = background_ctx_factory(ctx.business_id, use_case="customer_care", parent=ctx)
    read = factory(uuid.uuid4())
    message_id = uuid.UUID(str(payload["message_id"]))
    recorded = recording.record_early_result(
        read,
        recording.RunMeta(
            use_case="customer_care",
            prompt=load_prompt("customer_care_message", 1),
            trigger_type="job",
            trigger_key=f"care-triage:{message_id}",
        ),
        needs_human=True,
        result={"classification": str(payload["category"]), "requires_person": True},
    )
    read = replace(read, causation_id=recorded.run_id)
    with transaction(read) as db:
        if not enabled(db, read, "customer_care", lock=True):
            return
        source = conversations.get_message_context(db, read, message_id)
        conversations.lock_for_customer(db, read, source.conversation_id)
        source = conversations.get_message_context(db, read, message_id)
        if source.author_kind not in {"visitor", "customer"}:
            return
        if source.customer_id:
            contact = crm.customer_care_contact(db, read, source.customer_id)
            if contact is None or contact.status != "active":
                return
        lock_subject(db, read, f"case:{message_id}")
        case = db.scalar(
            select(CareCase).where(CareCase.business_id == ctx.business_id, CareCase.source_message_id == message_id)
        )
        if case and (case.proposal_id or case.status == "resolved"):
            return
        last = scheduling.latest_completed_appointment(db, read, source.customer_id) if source.customer_id else None
        invoice = (
            sales.care_invoice_reference(db, read, source.customer_id, last.id if last else None)
            if source.customer_id
            else None
        )
        if case is None:
            case = CareCase(
                business_id=ctx.business_id,
                customer_id=source.customer_id,
                conversation_id=source.conversation_id,
                source_message_id=message_id,
                category=str(payload["category"]),
                priority=str(payload.get("priority", "high")),
                status="open",
                appointment_id=last.id if last else None,
                invoice_id=invoice,
            )
            db.add(case)
            db.flush()
        else:
            case.customer_id = source.customer_id
            case.appointment_id, case.invoice_id = last.id if last else None, invoice
        conversations.request_handoff(db, read, source.conversation_id, "Customer Care concern requires a person")
        case_id, appointment_id, invoice_id = case.id, case.appointment_id, case.invoice_id
    if source.customer_id is None:
        return  # Staff must identify the customer; the Work item is still durable.
    with workflow_scope(read):
        proposal = prepare(
            ctx.business_id,
            "complaint_reply",
            customer_id=source.customer_id,
            appointment_id=appointment_id,
            source_message_id=message_id,
            invoice_id=invoice_id,
        )
    if proposal:
        with transaction(read) as db:
            row = db.scalar(
                select(CareCase)
                .where(CareCase.business_id == ctx.business_id, CareCase.id == case_id)
                .with_for_update()
            )
            if row:
                row.proposal_id = proposal
