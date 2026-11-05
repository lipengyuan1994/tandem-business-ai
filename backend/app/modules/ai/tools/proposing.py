"""What every hold and propose tool shares (AI-07): plain-text arguments, evidence, proposal creation.

- Model-written text is stored as given, length-limited by the argument model, and refused when it contains HTML.
- Titles are built by the tools from looked-up data, never from model text.
- Every proposal carries evidence: the run's source message (when there is one), the tool's own lookup and the run's
  earlier tool results about the same records (`RunState.evidence`, filled by the read tools).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import AfterValidator
from sqlalchemy.orm import Session

from app.modules.approvals import public as approvals
from app.modules.approvals.public import Evidence
from app.modules.conversations import public as conversations
from app.platform.context import Context
from app.platform.errors import DomainError

from .base import RunState
from .common import MAX_EVIDENCE, MAX_EXCERPT_CHARS, cite, redact_contacts

MAX_TITLE_CHARS = 200  # action_proposals.title holds 300
_HTML = re.compile(r"<\s*/?\s*[A-Za-z!][^<>]*>|&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);")


def _plain(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    if _HTML.search(value):
        raise ValueError("text must be plain, without HTML tags or entities")
    return value


PlainText = Annotated[str, AfterValidator(_plain)]
"""Model-written text: non-blank, no HTML. Combine with `Field(max_length=...)` at the use site."""


def excerpt(text: str) -> str:
    return text if len(text) <= MAX_EXCERPT_CHARS else text[: MAX_EXCERPT_CHARS - 1] + "…"


def source_message(db: Session, ctx: Context, state: RunState) -> Evidence | None:
    """The message that triggered the run (intake), as evidence; contact values in it are redacted."""
    if state.trigger_message_id is None:
        return None
    try:
        body = conversations.get_message_context(db, ctx, state.trigger_message_id).body
    except DomainError:  # not readable in this context: cite the id only
        body = ""
    return Evidence(
        kind="message", ref_type="message", ref_id=state.trigger_message_id,
        excerpt=redact_contacts(body, MAX_EXCERPT_CHARS) or "",
    )  # fmt: skip


def run_conversation(db: Session, ctx: Context, state: RunState) -> uuid.UUID | None:
    """The conversation a proposal belongs to when the tool has none of its own: the trigger message's (intake), else
    the only conversation read in this run. Linking it lets the intake duplicate guard find the proposal."""
    if state.trigger_message_id is not None:
        try:
            return conversations.get_message_context(db, ctx, state.trigger_message_id).conversation_id
        except DomainError:
            pass
    if len(state.read_conversation_ids) == 1:
        return next(iter(state.read_conversation_ids))
    return None


def tool_result(tool: str, ref_id: uuid.UUID | None, data: Any) -> Evidence:
    """Evidence for the data a propose tool looked up itself (the same shape the read tools cite)."""
    scratch = RunState(run_id=uuid.UUID(int=0), trigger_key="")
    cite(scratch, tool, ref_id, data)
    return scratch.evidence[0]


def note(text: str) -> Evidence:
    return Evidence(kind="note", excerpt=excerpt(text))


def gather(
    db: Session, ctx: Context, state: RunState, own: Iterable[Evidence], related_ids: Iterable[uuid.UUID | None]
) -> list[Evidence]:
    """Source message, then the tool's own evidence, then earlier results of this run about the same records."""
    out: list[Evidence] = []
    if (message := source_message(db, ctx, state)) is not None:
        out.append(message)
    out.extend(own)
    ids = {i for i in related_ids if i is not None}
    for earlier in reversed(state.evidence):
        if len(out) >= MAX_EVIDENCE:
            break
        if earlier.ref_id in ids and earlier not in out:
            out.append(earlier)
    return out[:MAX_EVIDENCE]


def propose(
    db: Session,
    ctx: Context,
    *,
    action_type: str,
    payload: dict[str, Any],
    title: str,
    evidence: list[Evidence],
    conversation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create the proposal in the caller's transaction (execute_tool records the effect in the same one)."""
    title = title if len(title) <= MAX_TITLE_CHARS else title[: MAX_TITLE_CHARS - 1] + "…"
    ref = approvals.create_proposal(
        db, ctx, action_type=action_type, payload=payload, title=title, evidence=evidence,
        conversation_id=conversation_id,
    )  # fmt: skip
    return {
        "proposal_id": ref.id,
        "title": title,
        "status": ref.status,
        "needs_approval": ref.status == "pending_review",
    }
