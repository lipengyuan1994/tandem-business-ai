"""Message building for the agent loop (contracts §8): system prompt + tool catalogue, the task with its fenced
context blocks, then pairs of (assistant step, user tool result). `RunSpec.server_facts` is never rendered."""

from __future__ import annotations

import json
from typing import Any

from ..gateway.types import ChatMessage
from .fencing import fence
from .prompts import Prompt
from .protocol import ContextBlock, RunSpec

TOOL_RESULT_PREFIX = "Tool result:\n"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def build_messages(prompt: Prompt, tool_catalog: list[dict[str, Any]], spec: RunSpec) -> list[ChatMessage]:
    """[system(prompt.text + "\\n\\nTools:\\n" + json(tool_catalog)), user(spec.task + "\\n\\n" + rendered blocks)]"""
    system = prompt.text + "\n\nTools:\n" + _json(tool_catalog)
    user = "\n\n".join([spec.task, *(fence(b) for b in spec.context_blocks)])
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def append_step(messages: list[ChatMessage], step_json: str, observation: dict[str, Any]) -> None:
    """assistant(step_json), user("Tool result:\\n" + fence(observation as untrusted, label="tool_result"))"""
    block = ContextBlock(label="tool_result", text=_json(observation), untrusted=True)
    messages.append(ChatMessage(role="assistant", content=step_json))
    messages.append(ChatMessage(role="user", content=TOOL_RESULT_PREFIX + fence(block)))


def estimate_tokens(messages: list[ChatMessage]) -> int:
    return sum(len(m.content) for m in messages) // 4


def trim_to_budget(messages: list[ChatMessage], max_tokens: int) -> list[ChatMessage]:
    """Estimate len(text)//4; drop the oldest (assistant step, tool result) pairs first; never the system message or
    the first user message. Returns a new list (the caller keeps it as the conversation from then on)."""
    out = list(messages)
    while estimate_tokens(out) > max_tokens and len(out) > 3:
        del out[2:4]  # the oldest pair after [system, task]
    return out


__all__ = ["append_step", "build_messages", "estimate_tokens", "trim_to_budget"]
