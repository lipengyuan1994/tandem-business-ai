"""Proposal metadata events shared by agent runners and streaming consumers."""

import uuid
from collections.abc import Callable
from typing import Any

from .protocol import StepEvent


def collect_proposal(
    observation: dict[str, Any],
    proposal_ids: list[uuid.UUID],
    titles: list[str],
    emit: Callable[[StepEvent], None],
    run_id: uuid.UUID,
) -> None:
    data = observation.get("data")
    if not observation.get("ok") or not isinstance(data, dict) or "proposal_id" not in data:
        return
    try:
        proposal_id = uuid.UUID(str(data["proposal_id"]))
    except ValueError:
        return
    if proposal_id in proposal_ids:
        return
    title = str(data.get("title") or "")[:200]
    proposal_ids.append(proposal_id)
    titles.append(title)
    emit(StepEvent(kind="proposal", run_id=run_id, proposal_id=proposal_id, proposal_title=title or None))
