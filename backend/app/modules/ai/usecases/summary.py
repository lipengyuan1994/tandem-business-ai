"""U3: summarize the last 30 messages and propose a reply through the shared tool path."""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel

from app.modules.conversations import public as conversations
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import DomainError

from ..agent.contexts import staff_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router
from ..schemas import ThreadSummary
from ..structured import complete
from ..tools.base import RunState
from ..tools.common import cite, redact_contacts
from ..tools.registry import execute_tool, get_tool


class SummaryOut(BaseModel):
    run_id: uuid.UUID
    summary: ThreadSummary
    proposal_id: uuid.UUID


def run(ctx: Context, conversation_id: uuid.UUID, *, router: Router | None = None) -> SummaryOut:
    ctx.require("ai.assistant.use")
    ctx.require("conversations.thread.read")
    assert ctx.business_id is not None and ctx.user_id is not None
    factory = staff_ctx_factory(ctx.business_id, ctx.user_id, ctx.permissions, "summary")
    with transaction(ctx) as db:
        thread = conversations.get_thread(db, ctx, conversation_id, last_n=30)
    data = {
        "channel": thread.channel,
        "status": thread.status,
        "visitor_name": redact_contacts(thread.visitor_name),
        "messages": [
            {"author_kind": m.author_kind, "direction": m.direction, "body": redact_contacts(m.body),
             "created_at": m.created_at.isoformat()}
            for m in thread.messages
        ],
    }  # fmt: skip

    def valid_reply(value: ThreadSummary) -> None:
        # The same constraints as the eventual tool call, so a bad draft gets the one structured repair.
        get_tool("propose_reply").args_model.model_validate(
            {"conversation_id": conversation_id, "text": value.suggested_reply}
        )

    trigger_key = f"summary:{conversation_id}:{uuid.uuid4()}"
    run_id, value = complete(
        factory, use_case="summary", prompt=load_prompt("summary"),
        facts=[ContextBlock(label="conversation", source_id=conversation_id,
                            text=json.dumps(data, ensure_ascii=False), untrusted=True)],
        output_model=ThreadSummary, trigger_type="api", trigger_key=trigger_key, router=router,
        validate=valid_reply,
    )  # fmt: skip
    state = RunState(run_id=run_id, trigger_key=trigger_key, read_conversation_ids={conversation_id})
    cite(state, "get_conversation", conversation_id, data)
    observation = execute_tool(
        factory(run_id), "propose_reply", {"conversation_id": str(conversation_id), "text": value.suggested_reply},
        ["propose_reply"], state,
    )  # fmt: skip
    if not observation["ok"]:
        raise DomainError(observation["error"], observation.get("detail"))
    return SummaryOut(run_id=run_id, summary=value, proposal_id=uuid.UUID(str(observation["data"]["proposal_id"])))
