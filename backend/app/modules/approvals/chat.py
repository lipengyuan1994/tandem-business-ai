"""Explicit requester approvals; thread ownership is verified by the AI module before calling here."""

from __future__ import annotations

import uuid
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.identity import public as identity
from app.modules.org import public as org
from app.platform import clock
from app.platform.canonical import sha256_hex
from app.platform.context import Context
from app.platform.errors import Conflict, DomainError, Forbidden, NotFound
from app.platform.registry import ChatMoney, ExecutorSpec

from .decisions import record_decision
from .models import (
    ActionProposal,
    ApprovalDecision,
    ChatConfirmCounter,
    ChatConfirmReservation,
    ExecutionAttempt,
    ProposalRevision,
)
from .schemas import ChatCard


def second_person(db: Session, ctx: Context, spec: ExecutorSpec, payload: object) -> bool:
    return spec.second_person(db, ctx, payload) if callable(spec.second_person) else spec.second_person


def _day(db: Session, ctx: Context) -> date:
    return clock.now().astimezone(ZoneInfo(org.get_business_brief(db, ctx).timezone)).date()


def _usage(db: Session, ctx: Context, money: ChatMoney, *, reserve: bool) -> None:
    if money.total_minor < 0 or money.total_minor > 50000 or money.currency != org.get_business_brief(db, ctx).currency:
        raise Forbidden("approvals.second_person_required")
    day = _day(db, ctx)
    if reserve:
        db.execute(
            insert(ChatConfirmCounter)
            .values(business_id=ctx.business_id, day=day, total_minor=0)
            .on_conflict_do_nothing(index_elements=["business_id", "day"])
        )
    stmt = select(ChatConfirmCounter).where(
        ChatConfirmCounter.business_id == ctx.business_id, ChatConfirmCounter.day == day
    )
    counter = db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if reserve else stmt)
    reservation = db.scalar(
        select(ChatConfirmReservation).where(
            ChatConfirmReservation.business_id == ctx.business_id,
            ChatConfirmReservation.day == day,
            ChatConfirmReservation.subject == money.subject,
        )
    )
    delta = max(0, money.total_minor - (reservation.total_minor if reservation else 0))
    if (counter.total_minor if counter else 0) + delta > 100000:
        raise Conflict("approvals.chat_confirm_limit")
    if reserve:
        assert counter is not None
        counter.total_minor += delta
        if reservation is None:
            db.add(
                ChatConfirmReservation(
                    business_id=ctx.business_id, day=day, subject=money.subject, total_minor=money.total_minor
                )
            )
        else:
            reservation.total_minor += delta


def owned_proposal(db: Session, ctx: Context, proposal_id: uuid.UUID, *, lock: bool = False) -> ActionProposal:
    stmt = select(ActionProposal).where(
        ActionProposal.business_id == ctx.business_id,
        ActionProposal.id == proposal_id,
        ActionProposal.origin_kind == "assistant",
        ActionProposal.origin_user_id == ctx.user_id,
    )
    row = db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)
    if row is None or row.ai_run_id is None:
        raise NotFound("ai.proposal_not_in_thread")
    return row


def _eligible(
    db: Session, ctx: Context, proposal: ActionProposal, revision: ProposalRevision, *, reserve: bool = False
) -> None:
    from .service import executor_for

    if proposal.status != "pending_review":
        raise Conflict("approvals.not_pending")
    if proposal.expires_at <= clock.now():
        raise Conflict("approvals.expired")
    spec = executor_for(proposal.action_type)
    if (
        not ctx.has(spec.approve_permission)
        or ctx.user_id is None
        or ctx.business_id is None
        or not identity.has_permission_now(db, ctx.user_id, ctx.business_id, spec.approve_permission)
    ):
        raise Forbidden("approvals.not_authorized_to_approve", detail=spec.approve_permission)
    payload = spec.payload_model.model_validate(revision.payload)
    if second_person(db, ctx, spec, payload):
        raise Forbidden("approvals.second_person_required")
    spec.validate(db, ctx, payload)
    if sha256_hex(payload.model_dump(mode="json")) != revision.payload_hash:
        raise Conflict("approvals.preview_changed")
    if spec.chat_money is not None:
        _usage(db, ctx, spec.chat_money(db, ctx, payload), reserve=reserve)


def card(db: Session, ctx: Context, proposal_id: uuid.UUID, *, run_id: uuid.UUID) -> ChatCard:
    row = owned_proposal(db, ctx, proposal_id)
    if row.ai_run_id != run_id:
        raise NotFound("ai.proposal_not_in_thread")
    revision = db.scalar(
        select(ProposalRevision).where(
            ProposalRevision.business_id == ctx.business_id, ProposalRevision.id == row.current_revision_id
        )
    )
    assert revision is not None
    reason = None
    try:
        _eligible(db, ctx, row, revision)
    except DomainError as exc:
        reason = exc.code
    from .reading import delivery_state

    result = (
        db.scalar(
            select(ExecutionAttempt.result).where(
                ExecutionAttempt.business_id == ctx.business_id,
                ExecutionAttempt.proposal_id == row.id,
                ExecutionAttempt.revision_id == row.current_revision_id,
                ExecutionAttempt.status == "succeeded",
            )
        )
        or {}
    )
    refs = {
        key: uuid.UUID(str(value))
        for key, value in result.items()
        if key in {"reorder_draft_id", "purchase_order_id", "outbox_id"}
    }
    return ChatCard(
        id=row.id,
        title=row.title,
        action_type=row.action_type,
        preview=revision.preview,
        status=row.status,
        can_confirm=reason is None,
        cannot_confirm_reason=reason,
        revision_id=revision.id,
        payload_hash=revision.payload_hash,
        delivery_state=delivery_state(db, ctx, row.id),
        result=refs,
    )


def confirm(
    db: Session, ctx: Context, proposal_id: uuid.UUID, *, run_id: uuid.UUID, revision_id: uuid.UUID, payload_hash: str
) -> None:
    proposal = owned_proposal(db, ctx, proposal_id, lock=True)
    if proposal.ai_run_id != run_id:
        raise NotFound("ai.proposal_not_in_thread")
    revision = db.scalar(
        select(ProposalRevision).where(
            ProposalRevision.business_id == ctx.business_id, ProposalRevision.id == proposal.current_revision_id
        )
    )
    assert revision is not None
    if revision.id != revision_id or revision.payload_hash != payload_hash:
        raise Conflict("approvals.revision_changed", current_revision_id=str(revision.id))
    _eligible(db, ctx, proposal, revision, reserve=True)
    assert ctx.user_id is not None and ctx.business_id is not None
    record_decision(
        db,
        ctx,
        proposal,
        ApprovalDecision(
            business_id=ctx.business_id,
            proposal_id=proposal.id,
            revision_id=revision.id,
            decision="approve",
            decided_by_kind="user",
            decided_by=ctx.user_id,
            decided_via="chat",
            roles_at_decision=sorted(identity.active_roles(db, ctx.user_id, ctx.business_id)),
            policy_version=identity.POLICY_VERSION,
            payload_hash=revision.payload_hash,
            comment=None,
        ),
    )


def origin_run_id(db: Session, ctx: Context, proposal_id: uuid.UUID) -> uuid.UUID:
    row = owned_proposal(db, ctx, proposal_id)
    assert row.ai_run_id is not None
    return row.ai_run_id
