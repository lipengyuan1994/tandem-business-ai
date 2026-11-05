"""Message building for the agent loop (contracts §8): system prompt + tool catalogue, the task with its fenced
context blocks, then pairs of (assistant step, user tool result). `RunSpec.server_facts` is never rendered."""

from __future__ import annotations

import json
from typing import Any

from app.platform.errors import DomainError

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


def estimate_tokens(messages: list[ChatMessage], schema: dict[str, Any] | None = None) -> int:
    """Shared UTF-8 bytes/4 estimate, including roles, message framing and the response schema."""
    size = len(_json(schema or {}).encode("utf-8")) + 32
    size += sum(len(m.content.encode("utf-8")) + len(m.role) + 16 for m in messages)
    return (size + 3) // 4


def trim_to_budget(
    messages: list[ChatMessage], max_tokens: int, schema: dict[str, Any] | None = None
) -> list[ChatMessage]:
    """Drop the oldest complete tool exchange first. Preserve system/task, source fences and repair requests.

    If the retained context and schema cannot fit, fail before calling the model. Never slice a record or fence.
    """
    out = list(messages)
    while estimate_tokens(out, schema) > max_tokens:
        oldest = next(
            (
                i
                for i in range(2, len(out) - 1)
                if out[i].role == "assistant"
                and out[i + 1].role == "user"
                and out[i + 1].content.startswith(TOOL_RESULT_PREFIX)
            ),
            None,
        )
        if oldest is None:
            raise InputTooLarge()
        del out[oldest : oldest + 2]
    return out


__all__ = [
    "InputTooLarge",
    "append_step",
    "build_messages",
    "enforce_input_budget",
    "estimate_tokens",
    "trim_to_budget",
]


class InputTooLarge(DomainError):
    status = 413
    title = "Input too large"

    def __init__(self) -> None:
        super().__init__("ai.input_too_large", "The prompt, source records and schema exceed the input limit.")


def enforce_input_budget(messages: list[ChatMessage], schema: dict[str, Any], max_tokens: int) -> None:
    """Enforce the context's documented four-bytes/token estimate, including schema and framing.

    Count UTF-8 bytes so Unicode is not underestimated as one byte per character. This is an input estimate,
    not provider-specific tokenization; both initial calls and repairs use the same accounting.
    """
    if estimate_tokens(messages, schema) > max_tokens:
        raise InputTooLarge()
