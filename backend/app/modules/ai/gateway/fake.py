"""Test doubles and the dev-only `fake` provider (contracts §5, testing.md §1). No network, no model."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import yaml  # type: ignore[import-untyped]  # PyYAML, locked through uvicorn[standard]

from app.platform.config import get_settings

from .fake_embeddings import embed
from .types import ChatRequest, ChatResult, EmbedRequest, EmbedResult, ProviderError, TranscriptionRequest, Usage

if TYPE_CHECKING:
    from .router import Router

ScriptItem = str | ProviderError


def _result(text: str, provider: str, model: str) -> ChatResult:
    tokens = len(text) // 4
    return ChatResult(
        text=text,
        usage=Usage(input_tokens=tokens, output_tokens=tokens),
        provider=provider,
        model=model or "fake",
        latency_ms=1,
    )


class FakeGateway:
    """Implements the Adapter protocol from a script: each call pops the next item (a str is returned as the text,
    a ProviderError is raised), or calls the function with the request. `calls` records every request."""

    name = "fake"

    def embed(self, req: EmbedRequest) -> EmbedResult:
        return embed(self.name, req)

    def __init__(self, script: list[ScriptItem] | Callable[[ChatRequest], str]) -> None:
        self._script: list[ScriptItem] | None = None if callable(script) else list(script)
        self._fn: Callable[[ChatRequest], str] | None = script if callable(script) else None
        self.calls: list[ChatRequest] = []
        self._lock = threading.Lock()

    def chat(self, req: ChatRequest) -> ChatResult:
        with self._lock:
            self.calls.append(req.model_copy(deep=True))
            if self._fn is not None:
                item: ScriptItem = self._fn(req)
            else:
                assert self._script is not None
                if not self._script:
                    raise AssertionError(
                        f"FakeGateway script exhausted after {len(self.calls) - 1} call(s): the test scripted too few"
                    )
                item = self._script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return _result(item, self.name, req.model)

    def transcribe(self, req: TranscriptionRequest) -> ChatResult:
        result = self.chat(ChatRequest(model=req.model, messages=[], use_case="dictation_stt"))
        return result.model_copy(update={"usage": Usage(input_tokens=0, output_tokens=0, audio_seconds=8)})

    @property
    def remaining(self) -> int:
        return len(self._script) if self._script is not None else 0


class ScriptFileAdapter:
    """The `fake` provider kind (dev and e2e only): replays `AI_FAKE_SCRIPT_FILE`, a YAML mapping
    `{<use_case>: [<reply text>, ...]}`, in order per `ChatRequest.use_case`, cycling. Refused when APP_ENV=prod."""

    def __init__(self, name: str, path: Path) -> None:
        if get_settings().is_prod:
            raise RuntimeError("the fake provider is refused when APP_ENV=prod")
        self.name = name
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"{path.name}: cannot be read as YAML ({type(exc).__name__})") from None
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: (root): expected a mapping of use case to a list of replies")
        script: dict[str, list[str]] = {}
        for use_case, replies in data.items():
            if not isinstance(replies, list) or not replies or not all(isinstance(r, str) for r in replies):
                raise ValueError(f"{path.name}: {use_case}: expected a non-empty list of strings")
            script[str(use_case)] = list(replies)
        self._script = script
        self._next: dict[str, int] = {}
        self._lock = threading.Lock()

    def chat(self, req: ChatRequest) -> ChatResult:
        use_case = req.use_case or ""
        replies = self._script.get(use_case)
        if replies is None:
            raise ProviderError("bad_request", "no scripted reply for this use case")
        with self._lock:
            i = self._next.get(use_case, 0)
            self._next[use_case] = (i + 1) % len(replies)
        return _result(replies[i], self.name, req.model)

    def embed(self, req: EmbedRequest) -> EmbedResult:
        return embed(self.name, req)

    def transcribe(self, req: TranscriptionRequest) -> ChatResult:
        result = self.chat(ChatRequest(model=req.model, messages=[], use_case="dictation_stt"))
        return result.model_copy(update={"usage": Usage(input_tokens=0, output_tokens=0, audio_seconds=8)})


def fake_router(gateway: FakeGateway, *, grounding: FakeGateway | None = None) -> Router:
    """Fake routes with an independent checker. Without explicit checker output, grounding fails closed."""
    from .router import Router
    from .routes import ProviderCfg, RouteEntry, RoutesFile

    classes = ("agent", "fast_extract", "writer", "safety", "embed", "stt", "tts")
    routes = RoutesFile(
        providers={
            gateway.name: ProviderCfg(kind="fake", vendor="vultr"),
            "checker": ProviderCfg(kind="fake", vendor="vultr"),
        },
        routes={
            **{c: [RouteEntry(provider=gateway.name, model=f"fake-{c}", price_per_audio_minute=0)] for c in classes},
            "grounding": [RouteEntry(provider="checker", model="fake-grounding")],
        },
    )
    # Old generator scripts do not masquerade as independent checker evidence. Tests opt in with labelled output.
    checker = grounding or FakeGateway(['{"claims":[],"verdict":"unavailable"}'])
    return Router(routes, {gateway.name: gateway, "checker": checker})


__all__ = ["FakeGateway", "ScriptFileAdapter", "fake_router"]
