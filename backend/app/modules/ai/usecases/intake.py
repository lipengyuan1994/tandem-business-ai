"""U1 intake guards, deterministic matching and one staff outcome note per message.

Visitor contacts belong exclusively to server_facts, never to model context blocks.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import text

from app.modules.approvals import public as approvals
from app.modules.conversations import public as conversations
from app.modules.crm import public as crm
from app.modules.org import public as org
from app.platform import clock
from app.platform.context import system_context
from app.platform.db import transaction

from .. import recording
from ..agent.contexts import CtxFactory, background_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock, RunResult, RunSpec
from ..agent.runner import run_agent
from ..errors import AiError
from ..gateway.router import Router
from ..tools.common import redact_contacts
from . import safety


@dataclass(frozen=True)
class CustomerMatch:
    kind: Literal["matched", "new", "ambiguous"]
    customer_id: uuid.UUID | None = None
    candidates: tuple[str, ...] = ()


@dataclass
class IntakeState:
    message_id: uuid.UUID
    trigger_key: str
    business: org.BusinessBrief
    router: Router | None
    server_facts: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)


def _early_result(ctx_factory: CtxFactory, state: IntakeState, result: dict[str, Any]) -> RunResult:
    facts = {**state.extra, **result}
    recorded = recording.record_early_result(
        ctx_factory(uuid.uuid4()),
        recording.RunMeta(use_case="intake", prompt=load_prompt("intake"), trigger_type="job",
                          trigger_key=state.trigger_key),
        needs_human=True, result=facts,
    )  # fmt: skip
    # record_early_result persists facts but does not populate RunResult.extra.
    return recorded.model_copy(update={"extra": facts})


def _match_customer(message: conversations.MessageContext, business_id: uuid.UUID) -> CustomerMatch:
    if message.customer_id is not None:
        return CustomerMatch("matched", customer_id=message.customer_id)
    ctx = system_context(business_id, frozenset({"crm.customer.read"}))
    with transaction(ctx) as db:
        candidates = crm.find_customer_candidates(
            db, ctx, name=message.visitor_name, email=message.visitor_email, phone=message.visitor_phone
        )
    candidates = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
    if not candidates or candidates[0].score < 0.5:
        return CustomerMatch("new")
    best = Decimal(str(candidates[0].score))
    # Decimal keeps the frozen inclusive 0.1 boundary (e.g. 0.9 minus 0.8) exact.
    if best >= Decimal("0.8") and (len(candidates) == 1 or best - Decimal(str(candidates[1].score)) >= Decimal("0.1")):
        return CustomerMatch("matched", customer_id=candidates[0].customer_id)
    return CustomerMatch("ambiguous", candidates=tuple(candidate.display_name for candidate in candidates[:3]))


def run_agent_step(
    ctx_factory: CtxFactory, message_ctx: conversations.MessageContext, match: CustomerMatch, state: IntakeState
) -> RunResult:
    """Prepare one reviewed booking or clarification using server-owned identity facts."""
    ctx = ctx_factory(uuid.uuid4())
    with transaction(ctx) as db:
        opening_hours = org.get_opening_hours(db, ctx)
    local_now = clock.to_zone(clock.now(), state.business.timezone)
    customer = (
        f"matched customer {match.customer_id}"
        if match.kind == "matched"
        else "new customer (details are held by the system; refer to them as the visitor)"
    )
    spec = RunSpec(
        use_case="intake",
        prompt=load_prompt("intake"),
        tools=[
            "get_conversation", "get_customer", "list_services", "get_service_parts", "find_slots", "check_stock",
            "hold_slot", "propose_booking", "propose_reply",
        ],
        task=(
            f"Current business-local date and time: {local_now.isoformat()} ({state.business.timezone}).\n"
            f"Customer: {customer}.\nConversation id: {message_ctx.conversation_id}.\n"
            "Prepare one proposal for this inbound message."
        ),
        context_blocks=[
            ContextBlock(label="business_brief", text=state.business.model_dump_json(), untrusted=False),
            ContextBlock(
                label="opening_hours", untrusted=False,
                text=json.dumps({"timezone": state.business.timezone, "weekday_key": "0 = Monday",
                                 "hours": [h.model_dump(mode="json") for h in opening_hours]}),
            ),
            ContextBlock(label="inbound_message", source_id=state.message_id,
                         text=redact_contacts(message_ctx.body) or "", untrusted=True),
        ],
        trigger_type="job",
        trigger_key=state.trigger_key,
        trigger_message_id=state.message_id,
        server_facts=state.server_facts,
    )  # fmt: skip
    return run_agent(ctx_factory, spec, router=state.router)


def _outcome_text(result: RunResult) -> str:
    if result.proposal_ids:
        return f"Prepared for approval: {'; '.join(result.extra.get('proposal_titles', []))}."
    if result.status == "policy_blocked":
        return "AI is turned off for this business, so no proposal was prepared."
    if result.status == "budget_exceeded":
        return "Today's AI budget is used up, so no proposal was prepared."
    if result.status in {"failed", "step_limit"}:
        return "Could not prepare a proposal; please handle this message."
    reason = result.extra.get("reason")
    if reason == "open_proposal":
        return "A proposal for this conversation is already waiting in Approvals."
    if reason == "safety":
        return "This message was passed to a person without AI handling."
    if reason == "ambiguous_customer":
        names = ", ".join(result.extra["candidates"])
        return f"Several customers could match ({names}). Please link the right one."
    if result.final is not None and result.final.needs_human:
        return f"Needs a person: {result.final.message_to_staff}"
    return "Could not prepare a proposal; please handle this message."


def _outcome_note(
    ctx_factory: CtxFactory, conversation_id: uuid.UUID, message_id: uuid.UUID, result: RunResult
) -> None:
    marker = f"[intake:{message_id}]"
    ctx = ctx_factory(result.run_id)
    with transaction(ctx) as db:
        # Serialize marker checks and writes for concurrent retries, without module-internal queries.
        db.execute(text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": marker})
        thread = conversations.get_thread(db, ctx, conversation_id, last_n=2_147_483_647)
        if any(
            m.direction == "internal" and m.author_kind == "assistant" and m.body.endswith(marker)
            for m in thread.messages
        ):
            return
        conversations.add_internal_note(
            db, ctx, conversation_id, f"{_outcome_text(result)} {marker}", author_kind="assistant"
        )


def run(message_id: uuid.UUID, business_id: uuid.UUID, *, router: Router | None = None) -> RunResult:
    ctx_factory = background_ctx_factory(business_id)
    ctx = system_context(business_id, frozenset({"conversations.thread.read", "org.business.read"}))
    with transaction(ctx) as db:
        message = conversations.get_message_context(db, ctx, message_id)
        brief = org.get_business_brief(db, ctx)
    guard_ctx = system_context(business_id, frozenset({"approvals.proposal.read"}))
    with transaction(guard_ctx) as db:
        open_proposals = approvals.find_open_proposals(
            db, guard_ctx, conversation_id=message.conversation_id,
            action_types=["scheduling.book_appointment", "conversations.send_reply"],
        )  # fmt: skip
    state = IntakeState(
        message_id=message_id, trigger_key=f"intake:{message_id}", business=brief, router=router,
        server_facts={"visitor_name": message.visitor_name, "visitor_email": message.visitor_email,
                      "visitor_phone": message.visitor_phone},
    )  # fmt: skip
    result: RunResult | None = None
    if open_proposals:
        result = _early_result(ctx_factory, state, {"reason": "open_proposal"})
    elif message.channel == "web_chat":
        try:
            verdict = safety.screen(ctx_factory, message.body, state.trigger_key, router=router)
        except AiError:
            verdict = None
        if verdict is None:
            state.extra["safety_unchecked"] = True
        elif verdict.flagged:
            with transaction(ctx) as db:
                conversations.request_handoff(db, ctx, message.conversation_id, reason="Message needs a person")
            result = _early_result(ctx_factory, state, {"reason": "safety"})
    if result is None:
        match = _match_customer(message, business_id)
        if match.kind == "ambiguous":
            result = _early_result(
                ctx_factory, state, {"reason": "ambiguous_customer", "candidates": list(match.candidates)}
            )
        else:
            result = run_agent_step(ctx_factory, message, match, state)
            result = result.model_copy(update={"extra": {**result.extra, **state.extra}})
    _outcome_note(ctx_factory, message.conversation_id, message_id, result)
    return result
