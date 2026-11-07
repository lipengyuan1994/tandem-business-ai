"""Owner-written reply rules, checked outside the proposal transaction and closed on failure."""

from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from app.modules.conversations import public as conversations
from app.modules.org import public as org
from app.platform.context import Context
from app.platform.db import transaction

from .agent.contexts import CtxFactory
from .agent.prompts import load_prompt
from .agent.protocol import ContextBlock
from .errors import AiError
from .gateway.router import Router
from .tools.base import RunState


def check(
    factory: CtxFactory,
    *,
    rules: str | None,
    message: str,
    reply: str,
    trigger_key: str,
    router: Router | None = None,
) -> conversations.ReplyGuardrailResult | None:
    from .structured import complete

    if not rules:
        return None
    try:
        _, value = complete(
            factory,
            use_case="reply_guardrail",
            prompt=load_prompt("reply_guardrail", 1),
            facts=[
                ContextBlock(label="owner_rules", text=rules, untrusted=False),
                ContextBlock(label="customer_message", text=message, untrusted=True),
                ContextBlock(label="drafted_reply", text=reply, untrusted=True),
            ],
            output_model=conversations.ReplyGuardrailResult,
            trigger_type="job",
            trigger_key=f"reply_guardrail:{trigger_key}",
            router=router,
        )
    except AiError:
        return None
    return value


def check_proposal(ctx: Context, arguments: dict[str, Any], state: RunState, router: Router) -> None:
    from .tools.proposals import ProposeReplyArgs

    state.reply_guardrail = None
    state.guardrail_hash = None
    state.guardrail_checked_reply = None
    try:
        args = ProposeReplyArgs.model_validate(arguments)
    except ValidationError:
        return  # execute_tool owns argument errors
    if state.trigger_message_id is None:
        return
    with transaction(ctx) as db:
        rules = org.get_settings(db, ctx).ai_reply_guardrail
        source = conversations.get_message_context(db, ctx, state.trigger_message_id)
    reply = "A person will follow up here." if args.knowledge_missing else args.text
    state.guardrail_hash = org.reply_guardrail_hash(rules)
    state.guardrail_checked_reply = reply
    state.reply_guardrail = check(
        lambda run_id: replace(
            ctx,
            ai_run_id=run_id,
            causation_id=ctx.ai_run_id,
            causing_agent_key=ctx.agent_key,
            chain_depth=ctx.chain_depth + 1,
        ),
        rules=rules,
        message=source.body,
        reply=reply,
        trigger_key=state.trigger_key,
        router=router,
    )
