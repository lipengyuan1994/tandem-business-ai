"""Agent protocol (contracts §8): the JSON step the model returns, the run specification and the run result."""

from __future__ import annotations

import copy
import uuid
from functools import cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..schemas import RunStatus, UseCaseId
from .prompts import Prompt


class ToolCallStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call"]
    tool: str
    arguments: dict[str, Any]
    reason: str = Field(max_length=300)


class FinalStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["final"]
    message_to_staff: str = Field(max_length=1200)
    proposal_ids: list[uuid.UUID] = []
    needs_human: bool = False


AgentStep = Annotated[ToolCallStep | FinalStep, Field(discriminator="kind")]
AGENT_STEP_ADAPTER: TypeAdapter[ToolCallStep | FinalStep] = TypeAdapter(AgentStep)


@cache
def _schema() -> dict[str, Any]:
    return AGENT_STEP_ADAPTER.json_schema()


def agent_step_json_schema() -> dict[str, Any]:
    """JSON schema of one AgentStep (a fresh copy: callers may not mutate the cached one)."""
    return copy.deepcopy(_schema())


class ContextBlock(BaseModel):
    label: str
    source_id: uuid.UUID | None = None
    text: str
    untrusted: bool


class RunSpec(BaseModel):
    use_case: UseCaseId
    prompt: Prompt
    tools: list[str]
    task: str  # instruction written by code; never contains untrusted text
    context_blocks: list[ContextBlock]
    trigger_type: Literal["event", "job", "api", "cron"]
    trigger_key: str
    trigger_message_id: uuid.UUID | None = None
    server_facts: dict[str, Any] = {}  # copied into RunState.server_facts; NEVER rendered into messages


class StepEvent(BaseModel):  # also the SSE payload (§14)
    kind: Literal["step", "proposal", "final", "error"]
    run_id: uuid.UUID
    tool: str | None = None
    status_text: str | None = None
    proposal_id: uuid.UUID | None = None
    proposal_title: str | None = None
    text: str | None = None
    error_code: str | None = None


class RunResult(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    final: FinalStep | None
    proposal_ids: list[uuid.UUID]
    error_code: str | None = None
    extra: dict[str, Any] = {}  # use-case facts, e.g. {"safety_unchecked": true}


__all__ = [
    "AGENT_STEP_ADAPTER",
    "AgentStep",
    "ContextBlock",
    "FinalStep",
    "RunResult",
    "RunSpec",
    "RunStatus",
    "StepEvent",
    "ToolCallStep",
    "agent_step_json_schema",
]
