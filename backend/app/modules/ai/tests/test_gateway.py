"""Gateway adapters and FakeGateway (AI-02). No network: httpx.MockTransport with hand-written provider fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog

from app.modules.ai.gateway import anthropic as anthropic_mod
from app.modules.ai.gateway import openai_compat as openai_mod
from app.modules.ai.gateway.anthropic import AnthropicAdapter
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.openai_compat import OpenAICompatAdapter
from app.modules.ai.gateway.types import ChatMessage, ChatRequest, ProviderError
from app.platform.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
SECRET_TEXT = "Ada Lovelace brake squeal on Tuesday"  # stands in for customer content that must never be logged

Handler = Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> dict[str, Any]:
    return dict(json.loads((FIXTURES / f"{name}.json").read_text()))


def reply(name: str, status: int = 200, headers: dict[str, str] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=fixture(name), headers=headers)

    return handler


class Recorder:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def body(self, i: int = -1) -> dict[str, Any]:
        return dict(json.loads(self.requests[i].content))


def mock_http(monkeypatch: pytest.MonkeyPatch, module: Any, handler: Handler) -> Recorder:
    recorder = Recorder(handler)
    calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> httpx.Client:
        calls.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(recorder))

    monkeypatch.setattr(module, "client", factory)
    recorder.factory_calls = calls  # type: ignore[attr-defined]
    return recorder


def req(json_mode: str = "text", schema: dict[str, Any] | None = None, **kw: Any) -> ChatRequest:
    return ChatRequest(
        model="example-model-instruct",
        messages=[
            ChatMessage(role="system", content="You are the intake assistant."),
            ChatMessage(role="user", content=SECRET_TEXT),
        ],
        json_schema=schema,
        json_mode=json_mode,  # type: ignore[arg-type]
        **kw,
    )


def oa() -> OpenAICompatAdapter:
    return OpenAICompatAdapter("vultr", "https://api.vultrinference.example.com/v1", "test-key-not-real")


def test_openai_compat_parses_text_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = mock_http(monkeypatch, openai_mod, reply("openai_compat_json_reply"))
    result = oa().chat(req())
    assert result.text == '{"type": "final", "text": "Proposed a brake service for Ada"}'
    assert (result.usage.input_tokens, result.usage.output_tokens) == (412, 37)
    assert (result.provider, result.model) == ("vultr", "example-model-instruct")
    assert result.latency_ms >= 0
    sent = rec.requests[0]
    assert str(sent.url) == "https://api.vultrinference.example.com/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer test-key-not-real"
    body = rec.body()
    assert body["model"] == "example-model-instruct"
    assert body["max_tokens"] == 800 and body["temperature"] == 0.2
    assert "response_format" not in body  # text mode
    assert body["messages"][1] == {"role": "user", "content": SECRET_TEXT}


def test_openai_compat_no_key_sends_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = mock_http(monkeypatch, openai_mod, reply("openai_compat_text_reply"))
    OpenAICompatAdapter("llama", "http://llama.internal:8080/v1", None, local=True).chat(req())
    assert "authorization" not in rec.requests[0].headers
    assert rec.factory_calls == [{"local": True}]  # type: ignore[attr-defined]


def test_openai_compat_json_schema_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = mock_http(monkeypatch, openai_mod, reply("openai_compat_json_reply"))
    oa().chat(req("json_schema", SCHEMA))
    body = rec.body()
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "out", "schema": SCHEMA, "strict": True},
    }
    assert body["messages"][0]["content"] == "You are the intake assistant."  # no schema text appended


def test_openai_compat_json_object_fallback_appends_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = mock_http(monkeypatch, openai_mod, reply("openai_compat_json_reply"))
    oa().chat(req("json_object", SCHEMA))
    body = rec.body()
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"] == (
        "You are the intake assistant." + "\n\nReturn JSON matching this schema:\n" + json.dumps(SCHEMA)
    )
    assert body["messages"][1]["content"] == SECRET_TEXT


def test_json_object_without_system_message_adds_one(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = mock_http(monkeypatch, openai_mod, reply("openai_compat_json_reply"))
    oa().chat(
        ChatRequest(messages=[ChatMessage(role="user", content="hi")], json_schema=SCHEMA, json_mode="json_object")
    )
    messages = rec.body()["messages"]
    assert messages[0]["role"] == "system" and messages[0]["content"].startswith("Return JSON matching this schema:")


@pytest.mark.parametrize(
    ("handler", "kind", "retry_after"),
    [
        (reply("openai_compat_rate_limited", 429, {"retry-after": "7"}), "rate_limited", 7.0),
        (reply("openai_compat_rate_limited", 429), "rate_limited", None),
        (reply("openai_compat_server_error", 500), "server_error", None),
        (reply("openai_compat_server_error", 503), "server_error", None),
        (reply("openai_compat_unauthorized", 401), "auth", None),
        (reply("openai_compat_unauthorized", 403), "auth", None),
        (reply("openai_compat_bad_request", 400), "bad_request", None),
        (reply("openai_compat_bad_request", 422), "bad_request", None),
        (reply("openai_compat_malformed"), "server_error", None),
        (lambda r: httpx.Response(200, content=b"<html>not json</html>"), "server_error", None),
        (lambda r: httpx.Response(200, json=[1, 2]), "server_error", None),
        (lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]}), "server_error", None),
    ],
)
def test_error_mapping_429_5xx_401_timeout_malformed(
    monkeypatch: pytest.MonkeyPatch, handler: Handler, kind: str, retry_after: float | None
) -> None:
    mock_http(monkeypatch, openai_mod, handler)
    with pytest.raises(ProviderError) as err:
        oa().chat(req())
    assert err.value.kind == kind
    assert err.value.retry_after_s == retry_after
    assert SECRET_TEXT not in str(err.value) and SECRET_TEXT not in err.value.detail


def test_retry_after_http_date() -> None:
    assert openai_mod.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert openai_mod.retry_after_seconds("soon") is None
    assert openai_mod.retry_after_seconds("2.5") == 2.5


def test_timeout_and_connection_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def slow(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    mock_http(monkeypatch, openai_mod, slow)
    with pytest.raises(ProviderError) as err:
        oa().chat(req(timeout_s=4.0))
    assert err.value.kind == "timeout"
    assert seen[0].extensions["timeout"]["read"] == 4.0  # per-call timeout from the request

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    mock_http(monkeypatch, openai_mod, refused)
    with pytest.raises(ProviderError) as err:
        oa().chat(req())
    assert err.value.kind == "unavailable"


def test_egress_denied_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real allowlisted client: the request hook refuses the host before any connection is attempted.
    monkeypatch.setattr(get_settings(), "egress_allowlist", "api.allowed.example.com")
    adapter = OpenAICompatAdapter("openai", "https://api.not-allowed.example.com/v1", "test-key-not-real")
    with pytest.raises(ProviderError) as err:
        adapter.chat(req())
    assert err.value.kind == "unavailable"
    anthropic = AnthropicAdapter("anthropic", "https://api.not-allowed.example.com", "test-key-not-real")
    with pytest.raises(ProviderError) as err:
        anthropic.chat(req())
    assert err.value.kind == "unavailable"


def test_anthropic_parses_and_prefills_json(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = mock_http(monkeypatch, anthropic_mod, reply("anthropic_json_reply"))
    adapter = AnthropicAdapter("anthropic", "https://api.anthropic.example.com", "test-key-not-real")
    result = adapter.chat(req("json_object", SCHEMA))
    assert json.loads(result.text) == {"type": "final", "text": "Proposed a brake service for Ada"}
    assert (result.usage.input_tokens, result.usage.output_tokens) == (380, 29)
    assert (result.provider, result.model) == ("anthropic", "example-model-instruct")
    sent = rec.requests[0]
    assert str(sent.url) == "https://api.anthropic.example.com/v1/messages"
    assert sent.headers["x-api-key"] == "test-key-not-real"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in sent.headers
    body = rec.body()
    assert body["system"] == (
        "You are the intake assistant." + "\n\nReturn JSON matching this schema:\n" + json.dumps(SCHEMA)
    )
    assert body["messages"] == [
        {"role": "user", "content": SECRET_TEXT},
        {"role": "assistant", "content": "{"},
    ]
    assert body["max_tokens"] == 800 and "response_format" not in body

    # json_schema mode is treated the same way (Anthropic has no response_format here).
    adapter.chat(req("json_schema", SCHEMA))
    assert rec.body()["messages"][-1] == {"role": "assistant", "content": "{"}


def test_anthropic_text_mode_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def text_reply(request: httpx.Request) -> httpx.Response:
        data = fixture("anthropic_json_reply")
        data["content"] = [{"type": "text", "text": "Plain answer"}]
        return httpx.Response(200, json=data)

    rec = mock_http(monkeypatch, anthropic_mod, text_reply)
    adapter = AnthropicAdapter("anthropic", "https://api.anthropic.example.com/v1", "test-key-not-real")
    result = adapter.chat(req())
    assert result.text == "Plain answer"
    assert str(rec.requests[0].url) == "https://api.anthropic.example.com/v1/messages"
    assert rec.body()["messages"] == [{"role": "user", "content": SECRET_TEXT}]

    for status, kind in ((529, "server_error"), (429, "rate_limited"), (401, "auth"), (400, "bad_request")):
        mock_http(monkeypatch, anthropic_mod, reply("anthropic_overloaded", status, {"retry-after": "3"}))
        with pytest.raises(ProviderError) as err:
            AnthropicAdapter("anthropic", "https://api.anthropic.example.com", "k").chat(req())
        assert err.value.kind == kind
    mock_http(monkeypatch, anthropic_mod, lambda r: httpx.Response(200, json={"content": "nope"}))
    with pytest.raises(ProviderError) as err:
        AnthropicAdapter("anthropic", "https://api.anthropic.example.com", "k").chat(req())
    assert err.value.kind == "server_error"


def test_no_message_content_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level("DEBUG")
    handlers: list[Handler] = [
        reply("openai_compat_json_reply"),
        reply("openai_compat_server_error", 500),
        reply("openai_compat_malformed"),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t", request=r)),
    ]
    with structlog.testing.capture_logs() as events:
        for handler in handlers:
            mock_http(monkeypatch, openai_mod, handler)
            try:
                oa().chat(req("json_object", SCHEMA))
            except ProviderError as exc:
                assert SECRET_TEXT not in repr(exc)
            mock_http(monkeypatch, anthropic_mod, handler)
            try:
                AnthropicAdapter("anthropic", "https://api.anthropic.example.com", "test-key-not-real").chat(req())
            except ProviderError as exc:
                assert SECRET_TEXT not in repr(exc)
    out = capsys.readouterr()
    everything = json.dumps(events, default=str) + caplog.text + out.out + out.err
    assert SECRET_TEXT not in everything
    assert "intake assistant" not in everything and "Proposed a brake" not in everything
    assert "test-key-not-real" not in everything
    # What is logged: provider, model, status code, latency.
    calls = [e for e in events if e["event"] == "ai_provider_call"]
    assert calls and {"provider", "model", "status", "latency_ms"} <= set(calls[0])
    errors = [e for e in events if e["event"] == "ai_provider_error"]
    assert {e.get("status") for e in errors} >= {500}


def test_adapter_repr_hides_key() -> None:
    assert "test-key-not-real" not in repr(oa())
    assert "test-key-not-real" not in repr(AnthropicAdapter("a", "", "test-key-not-real"))


def test_fake_gateway_script_and_calls() -> None:
    gw = FakeGateway(["first reply", ProviderError("rate_limited", retry_after_s=1.0), "x" * 40])
    r1 = gw.chat(req())
    assert r1.text == "first reply" and r1.provider == "fake" and r1.latency_ms == 1
    assert (r1.usage.input_tokens, r1.usage.output_tokens) == (len("first reply") // 4, len("first reply") // 4)
    with pytest.raises(ProviderError) as err:
        gw.chat(req())
    assert err.value.kind == "rate_limited"
    assert gw.chat(req()).usage.output_tokens == 10
    assert len(gw.calls) == 3 and gw.calls[0].messages[1].content == SECRET_TEXT

    echo = FakeGateway(lambda r: f"echo {r.use_case}")
    assert echo.chat(req(use_case="summary")).text == "echo summary"
    assert echo.calls[0].use_case == "summary"


def test_fake_gateway_raises_when_script_exhausted() -> None:
    gw = FakeGateway(["only one"])
    gw.chat(req())
    with pytest.raises(AssertionError, match="exhausted"):
        gw.chat(req())
