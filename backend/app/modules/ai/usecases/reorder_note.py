"""U4: explain inventory's suggestion, then propose its quantity through the shared reorder tool."""

from __future__ import annotations

import json
import uuid
from datetime import UTC

import structlog

from app.modules.approvals import public as approvals
from app.modules.inventory import public as inventory
from app.platform import clock
from app.platform.db import transaction
from app.platform.errors import DomainError

from ..agent.contexts import background_ctx_factory
from ..agent.prompts import load_prompt
from ..agent.protocol import ContextBlock
from ..gateway.router import Router
from ..schemas import ReorderNote
from ..structured import complete
from ..tools.base import RunState
from ..tools.common import cite, qty, redact_contacts
from ..tools.registry import execute_tool, get_tool
from .briefing import validate_numbers

log = structlog.get_logger(__name__)


def run(item_id: uuid.UUID, business_id: uuid.UUID, *, router: Router | None = None) -> uuid.UUID | None:
    """Return the proposal id, or None if an open intent exists, autonomy is off, or stock is no longer low.

    Every database transaction is short; the model call holds none. Daily trigger/effect keys keep retries
    idempotent, while the public pending lookup also catches proposals made by staff or on another day.
    """
    factory = background_ctx_factory(business_id, use_case="reorder_note")
    read_ctx = factory(uuid.uuid4())
    with transaction(read_ctx) as db:
        try:
            approvals.check_ai_proposal_allowed(db, read_ctx, "inventory.create_reorder_draft")
        except DomainError as exc:
            if exc.code != "approvals.autonomy_off":
                raise
            log.info("reorder_skipped", item_id=str(item_id), code=exc.code)
            return None
        if approvals.find_open_proposals(
            db, read_ctx, action_types=["inventory.create_reorder_draft"], item_id=item_id, include_executing=True
        ):
            return None
        if inventory.has_open_reorder_draft(db, read_ctx, item_id):
            return None
        suggestion = next((s for s in inventory.list_below_reorder(db, read_ctx) if s.item_id == item_id), None)
    if suggestion is None:
        return None
    data = {
        "item_name": redact_contacts(suggestion.item_name),
        "available": qty(suggestion.available),
        "reorder_point": qty(suggestion.reorder_point),
        "target_level": qty(suggestion.target_level),
        "suggested": qty(suggestion.suggested),
        "explanation": redact_contacts(suggestion.explanation),
    }
    facts = json.dumps(data, ensure_ascii=False)

    def validate(value: ReorderNote) -> None:
        validate_numbers(value.explanation, facts)
        # Use the real tool's plain-text constraints before accepting the output, so they also get one repair.
        get_tool("propose_reorder").args_model.model_validate(
            {"item_ids": [str(item_id)], "reason_text": value.explanation}
        )

    trigger_key = f"reorder_note:{item_id}:{clock.now().astimezone(UTC).date().isoformat()}"
    run_id, value = complete(
        factory, use_case="reorder_note", prompt=load_prompt("reorder_note"),
        facts=[ContextBlock(label="reorder_suggestion", source_id=item_id, text=facts, untrusted=True)],
        output_model=ReorderNote, trigger_type="job", trigger_key=trigger_key, router=router, validate=validate,
    )  # fmt: skip
    ctx = factory(run_id)
    # A person may have prepared a reorder while the writer was running.
    with transaction(ctx) as db:
        if approvals.find_open_proposals(
            db, ctx, action_types=["inventory.create_reorder_draft"], item_id=item_id, include_executing=True
        ):
            return None
        if inventory.has_open_reorder_draft(db, ctx, item_id):
            return None
    state = RunState(run_id=run_id, trigger_key=trigger_key)
    cite(state, "list_below_reorder", item_id, data)
    observation = execute_tool(
        ctx, "propose_reorder", {"item_ids": [str(item_id)], "reason_text": value.explanation},
        ["propose_reorder"], state,
    )  # fmt: skip
    if not observation["ok"]:
        if observation["error"] in {
            "approvals.autonomy_off",
            "inventory.open_reorder_draft",
            "ai.reorder_already_proposed",
            "inventory.reorder_changed",
            "ai.not_below_reorder",
        }:
            log.info("reorder_skipped", item_id=str(item_id), code=observation["error"])
            return None
        raise DomainError(observation["error"], observation.get("detail"))
    return uuid.UUID(str(observation["data"]["proposal_id"]))
