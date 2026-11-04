"""Provider-agnostic chat types (docs/roadmap/ai/contracts.md §5).

Message content is never logged and never put into an exception: `ProviderError.detail` carries only status codes,
provider and model names and short fixed reasons.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]
JsonMode = Literal["json_schema", "json_object", "text"]


class ChatMessage(BaseModel):
    role: Role
    content: str


class ChatRequest(BaseModel):
    model: str = ""  # filled by the router
    messages: list[ChatMessage]
    json_schema: dict[str, Any] | None = None
    # Filled by the router from the route entry: json_schema if RouteEntry.json_schema and json_schema is set;
    # json_object if json_schema is set; else text.
    json_mode: JsonMode = "text"
    max_output_tokens: int = 800
    temperature: float = 0.2
    timeout_s: float = 30.0  # the runner sets min(step_timeout_s, remaining run time)
    use_case: str | None = None  # informational; only the "fake" provider uses it to pick its script


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class ChatResult(BaseModel):
    text: str  # raw text; JSON parsing and validation are the caller's job
    usage: Usage
    provider: str
    model: str
    latency_ms: int


ProviderErrorKind = Literal["timeout", "rate_limited", "server_error", "auth", "bad_request", "unavailable"]
TRANSIENT_KINDS: frozenset[str] = frozenset({"timeout", "rate_limited", "server_error", "unavailable"})


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, detail: str = "", retry_after_s: float | None = None) -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind: ProviderErrorKind = kind
        self.detail = detail
        self.retry_after_s = retry_after_s

    @property
    def transient(self) -> bool:
        return self.kind in TRANSIENT_KINDS


class Adapter(Protocol):
    name: str  # provider key from the route file

    def chat(self, req: ChatRequest) -> ChatResult: ...


__all__ = [
    "TRANSIENT_KINDS",
    "Adapter",
    "ChatMessage",
    "ChatRequest",
    "ChatResult",
    "JsonMode",
    "ProviderError",
    "ProviderErrorKind",
    "Role",
    "Usage",
]
