"""Adapter for the Anthropic Messages API (`POST /v1/messages`). Contracts §5.

The system prompt goes into `system`. JSON is requested with the appended schema instruction plus an assistant
prefill `{`, which is prepended to the returned text. Message content is never logged or put into an exception.
"""

from __future__ import annotations

import json
from typing import Any

from app.platform.http import client

from .openai_compat import JSON_OBJECT_INSTRUCTION, HttpAdapterBase, int_field
from .types import ChatRequest, ChatResult, Usage

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicAdapter(HttpAdapterBase):
    def __init__(self, name: str, base_url: str, api_key: str | None) -> None:
        super().__init__(name, lambda: client())
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key or None

    def __repr__(self) -> str:  # never show the key
        return f"AnthropicAdapter(name={self.name!r}, base_url={self.base_url!r})"

    @property
    def url(self) -> str:
        # Accept both "https://api.anthropic.com" and "https://api.anthropic.com/v1" as base_url.
        return f"{self.base_url}/messages" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/messages"

    def wants_json(self, req: ChatRequest) -> bool:
        return req.json_mode != "text"

    def request_body(self, req: ChatRequest) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in req.messages if m.role == "system")
        if self.wants_json(req):
            instruction = (
                JSON_OBJECT_INSTRUCTION + json.dumps(req.json_schema)
                if req.json_schema is not None
                else "\n\nReturn only a JSON object."
            )
            system = (system + instruction) if system else instruction.lstrip()
        messages: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in req.messages if m.role != "system"
        ]
        if self.wants_json(req):
            messages.append({"role": "assistant", "content": "{"})
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_output_tokens,
            "temperature": req.temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system
        return body

    def chat(self, req: ChatRequest) -> ChatResult:
        headers = {"anthropic-version": ANTHROPIC_VERSION, "Accept": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        data, latency_ms = self._post(
            self.url, headers, self.request_body(req), model=req.model, timeout_s=req.timeout_s
        )
        try:
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise TypeError("content")
            text = "".join(b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text")
            if not isinstance(text, str):
                raise TypeError("text")
            usage = Usage(
                input_tokens=int_field(data["usage"], "input_tokens"),
                output_tokens=int_field(data["usage"], "output_tokens"),
            )
        except (KeyError, TypeError, AttributeError):
            raise self.malformed(req.model) from None
        if self.wants_json(req):
            text = "{" + text
        return ChatResult(text=text, usage=usage, provider=self.name, model=req.model, latency_ms=latency_ms)


__all__ = ["AnthropicAdapter"]
