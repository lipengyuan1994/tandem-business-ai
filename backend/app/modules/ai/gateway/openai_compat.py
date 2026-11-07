"""Adapter for OpenAI-compatible `POST {base_url}/chat/completions` (Vultr Serverless Inference, OpenAI, Gemini's
compatible endpoint, llama.cpp). Contracts §5.

Also holds the HTTP plumbing shared with the Anthropic adapter: the error mapping and the content-free call log.
HTTP goes only through `app.platform.http.client()` (egress allowlist), or `client(local=True)` for providers in
`LOCAL_INFERENCE_HOSTS`. Message content is never logged and never put into an exception.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from app.platform.http import EgressDenied, client

from .types import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    EmbedRequest,
    EmbedResult,
    ProviderError,
    TranscriptionRequest,
    Usage,
)

log = structlog.get_logger(__name__)

JSON_OBJECT_INSTRUCTION = "\n\nReturn JSON matching this schema:\n"


def retry_after_seconds(value: str | None) -> float | None:
    """`Retry-After` as seconds: delta-seconds or an HTTP date. None when absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def error_for_status(status: int, retry_after: str | None) -> ProviderError | None:
    """Contracts mapping: 429 → rate_limited, 5xx → server_error, 401/403 → auth, other 4xx → bad_request."""
    if status < 400:
        return None
    if status == 429:
        return ProviderError("rate_limited", f"HTTP {status}", retry_after_seconds(retry_after))
    if status >= 500:
        return ProviderError("server_error", f"HTTP {status}")
    if status in (401, 403):
        return ProviderError("auth", f"HTTP {status}")
    return ProviderError("bad_request", f"HTTP {status}")


class HttpAdapterBase:
    """Lazily creates one allowlisted httpx client per adapter (thread-safe; httpx clients may be shared)."""

    name: str

    def __init__(self, name: str, client_factory: Callable[[], httpx.Client]) -> None:
        self.name = name
        self._client_factory = client_factory
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _http(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._client_factory()
        return self._client

    def _post(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        *,
        model: str,
        timeout_s: float,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """POST JSON; returns (parsed body, latency ms) or raises ProviderError. Logs only metadata."""
        started = time.monotonic()
        timeout = httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))
        status: int | None = None
        try:
            if files is None:
                response = self._http().post(url, headers=headers, json=body, timeout=timeout)
            else:
                response = self._http().post(url, headers=headers, data=body, files=files, timeout=timeout)
            status = response.status_code
            error = error_for_status(status, response.headers.get("retry-after"))
            if error is not None:
                raise error
            try:
                data = response.json()
            except ValueError:
                raise ProviderError("server_error", "malformed response body") from None
            if not isinstance(data, dict):
                raise ProviderError("server_error", "malformed response body")
        except ProviderError as exc:
            self._log_error(model, status, exc.kind, started)
            raise
        except httpx.TimeoutException as exc:
            self._log_error(model, status, "timeout", started)
            raise ProviderError("timeout", "request timed out") from exc
        except EgressDenied as exc:
            self._log_error(model, status, "unavailable", started)
            raise ProviderError("unavailable", "egress denied") from exc
        except httpx.HTTPError as exc:  # connection refused, DNS, protocol errors
            self._log_error(model, status, "unavailable", started)
            raise ProviderError("unavailable", type(exc).__name__) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        log.info("ai_provider_call", provider=self.name, model=model, status=status, latency_ms=latency_ms)
        return data, latency_ms

    def _log_error(self, model: str, status: int | None, kind: str, started: float) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        log.warning(
            "ai_provider_error", provider=self.name, model=model, status=status, kind=kind, latency_ms=latency_ms
        )

    def malformed(self, model: str) -> ProviderError:
        log.warning("ai_provider_error", provider=self.name, model=model, kind="server_error", reason="malformed")
        return ProviderError("server_error", "malformed response body")


def with_schema_instruction(messages: list[ChatMessage], schema: dict[str, Any]) -> list[ChatMessage]:
    """Append the schema instruction to the system message (a system message is added when there is none)."""
    instruction = JSON_OBJECT_INSTRUCTION + json.dumps(schema)
    out = list(messages)
    for i, m in enumerate(out):
        if m.role == "system":
            out[i] = ChatMessage(role="system", content=m.content + instruction)
            return out
    return [ChatMessage(role="system", content=instruction.lstrip()), *out]


def int_field(data: Any, key: str) -> int:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise KeyError(key)
    return value


class OpenAICompatAdapter(HttpAdapterBase):
    def __init__(self, name: str, base_url: str, api_key: str | None, *, local: bool = False) -> None:
        super().__init__(name, lambda: client(local=local))
        self.base_url = base_url.rstrip("/")
        self.local = local
        self._api_key = api_key or None

    def __repr__(self) -> str:  # never show the key
        return f"OpenAICompatAdapter(name={self.name!r}, base_url={self.base_url!r}, local={self.local})"

    def request_body(self, req: ChatRequest) -> dict[str, Any]:
        messages = req.messages
        body: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_output_tokens,
            "temperature": req.temperature,
        }
        if req.json_mode == "json_schema" and req.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "out", "schema": req.json_schema, "strict": True},
            }
        elif req.json_mode != "text":
            body["response_format"] = {"type": "json_object"}
            if req.json_schema is not None:
                messages = with_schema_instruction(messages, req.json_schema)
        body["messages"] = [{"role": m.role, "content": m.content} for m in messages]
        return body

    def embed(self, req: EmbedRequest) -> EmbedResult:
        if not 1 <= len(req.inputs) <= 32:
            raise ProviderError("bad_request", "embedding batch must contain 1 to 32 inputs")
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data, latency = self._post(
            f"{self.base_url}/embeddings",
            headers,
            {"model": req.model, "input": req.inputs, "dimensions": 1024, "encoding_format": "float"},
            model=req.model,
            timeout_s=req.timeout_s,
        )
        try:
            rows = data["data"]
            if not isinstance(rows, list) or len(rows) != len(req.inputs):
                raise ValueError
            vectors: dict[int, list[float]] = {}
            for row in rows:
                index = int_field(row, "index")
                vector = row["embedding"]
                if index >= len(rows) or index in vectors or not isinstance(vector, list) or len(vector) != 1024:
                    raise ValueError
                if any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 3.4e38
                    for v in vector
                ):
                    raise ValueError
                if not any(vector):
                    raise ValueError
                vectors[index] = vector
            tokens = int_field(data["usage"], "prompt_tokens")
        except (KeyError, TypeError, ValueError, AttributeError):
            raise self.malformed(req.model) from None
        return EmbedResult(
            embeddings=[vectors[i] for i in range(len(rows))],
            usage=Usage(input_tokens=tokens, output_tokens=0),
            provider=self.name,
            model=req.model,
            latency_ms=latency,
        )

    def transcribe(self, req: TranscriptionRequest) -> ChatResult:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # Vultr rejects verbose_json; local whisper.cpp/faster-whisper servers use it for duration metadata.
        body = {"model": req.model, "response_format": "verbose_json" if self.local else "json"}
        if req.language:
            body["language"] = req.language
        data, latency = self._post(
            f"{self.base_url}/audio/transcriptions",
            headers,
            body,
            model=req.model,
            timeout_s=req.timeout_s,
            files={"file": ("dictation" + _audio_suffix(req.mime), req.audio, req.mime)},
        )
        text = data.get("text")
        seconds = data.get("usage", {}).get("seconds") if isinstance(data.get("usage"), dict) else None
        if seconds is None:
            seconds = data.get("duration")
        if (
            not isinstance(text, str)
            or not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise self.malformed(req.model)
        return ChatResult(
            text=text,
            usage=Usage(input_tokens=0, output_tokens=0, audio_seconds=seconds),
            provider=self.name,
            model=req.model,
            latency_ms=latency,
        )

    def chat(self, req: ChatRequest) -> ChatResult:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data, latency_ms = self._post(
            f"{self.base_url}/chat/completions",
            headers,
            self.request_body(req),
            model=req.model,
            timeout_s=req.timeout_s,
        )
        try:
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise TypeError("content")
            usage = Usage(
                input_tokens=int_field(data["usage"], "prompt_tokens"),
                output_tokens=int_field(data["usage"], "completion_tokens"),
            )
        except (KeyError, IndexError, TypeError, AttributeError):
            raise self.malformed(req.model) from None
        # The route's model name (not the provider's echo) so recording and pricing match the route entry.
        return ChatResult(text=text, usage=usage, provider=self.name, model=req.model, latency_ms=latency_ms)


def _audio_suffix(mime: str) -> str:
    return {
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
    }.get(mime, ".audio")


__all__ = ["OpenAICompatAdapter", "error_for_status", "retry_after_seconds"]
