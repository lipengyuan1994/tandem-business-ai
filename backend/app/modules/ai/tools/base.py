"""Tool and run-state types (docs/roadmap/ai/contracts.md §10)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.approvals.public import Evidence
from app.platform.context import Context

ToolKind = Literal["read", "hold", "propose"]


@dataclass
class RunState:
    run_id: uuid.UUID
    trigger_key: str
    trigger_message_id: uuid.UUID | None = None
    read_conversation_ids: set[uuid.UUID] = field(default_factory=set)
    # hold_id → {service_id, resource_id, starts_at, customer_id}
    holds: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    proposals: dict[str, uuid.UUID] = field(default_factory=dict)  # effect_key → proposal id
    evidence: list[Evidence] = field(default_factory=list)  # ≤ 10 tool results worth citing
    server_facts: dict[str, Any] = field(default_factory=dict)  # e.g. visitor contact fields; never shown to the model


@dataclass(frozen=True)
class Tool:
    """A function the model may call. `run` gets the open transaction of `execute_tool` and must not commit.

    Hold tools return `{"hold_id": ...}` and propose tools `{"proposal_id": ...}` in their data (plus anything else):
    `execute_tool` records that id in `ai_effects` in the same transaction. An args model may declare
    `effect_fields: ClassVar[tuple[str, ...]]`, the arguments that identify the effect (default: all of them).
    """

    name: str
    description: str
    kind: ToolKind
    permission: str
    args_model: type[BaseModel]  # extra="forbid"; NEVER a business_id field
    run: Callable[[Session, Context, BaseModel, RunState], dict[str, Any]]
