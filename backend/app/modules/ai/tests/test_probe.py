"""Probe coverage with synthetic catalog data and no network transport."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.modules.ai import probe
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.types import ProviderError

FINAL = '{"kind":"final","message_to_staff":"ok"}'
TOOL = '{"kind":"tool_call","tool":"list_services","arguments":{"query":"brake"},"reason":"Find services"}'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("The probe tests must never use a real network transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)


def test_probe_report_format(tmp_path):
    gateway = FakeGateway([FINAL, '{"kind":1}', TOOL, ProviderError("timeout", "private headers"), FINAL, FINAL])
    rows = probe.probe_models(gateway, ["chat-a", "chat-b"])
    md, machine = probe.write_report(
        tmp_path,
        provider="vultr",
        base_url="https://catalog.example.com/v1",
        catalog=[{"id": "chat-a", "owned_by": "example", "pricing": {"completion": 0.2}}],
        rows=rows,
        now=datetime(2026, 11, 4, 0, 15, tzinfo=UTC),
    )
    assert md.name == "2026-11-04-probe.md" and machine.name == "2026-11-04-probe.json"
    report = json.loads(machine.read_text())
    assert report["date"] == "2026-11-04" and report["base_url_host"] == "catalog.example.com"
    assert report["catalog"][0]["pricing"] == {"completion": 0.2}
    assert len(report["results"]) == 6
    first, invalid, tool, timeout, _, wrong_tool = report["results"]
    assert first["schema_valid"] and first["expected_output"]
    assert first["latency_ms"] == 1 and first["input_tokens"] == len(FINAL) // 4
    assert first["output_tokens"] == len(FINAL) // 4
    assert invalid["schema_valid"] is False and invalid["error_kind"] == "validation_error"
    assert tool["schema_valid"] and tool["expected_output"]
    assert wrong_tool["schema_valid"] and not wrong_tool["expected_output"]
    assert timeout["error_kind"] == "timeout" and timeout["input_tokens"] is None
    assert timeout["latency_ms"] >= 0
    text = md.read_text()
    assert "2026-11-04" in text and "catalog.example.com" in text
    assert "| chat-a | final | json_schema | yes | yes |" in text
    assert "| chat-a | tool_call | json_object | yes | yes |" in text
    assert "private headers" not in text + machine.read_text()
    assert [r.json_mode for r in gateway.calls] == ["json_schema", "json_object", "json_object"] * 2
    assert all(r.timeout_s == 60 and r.json_schema is not None for r in gateway.calls)
    assert gateway.calls[2].json_schema == probe.agent_step_json_schema()
    assert "list_services" in gateway.calls[2].messages[0].content


@pytest.mark.parametrize("value", [None, "0", "true"])
def test_probe_refuses_without_ai_live(monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("AI_LIVE", raising=False)
    else:
        monkeypatch.setenv("AI_LIVE", value)

    def forbidden(*args, **kwargs):
        pytest.fail("Refusal must precede route/key access or adapter construction")

    monkeypatch.setattr(probe, "load_routes_file", forbidden)
    monkeypatch.setattr(probe, "OpenAICompatAdapter", forbidden)
    assert probe.main([]) == 2
    assert "AI_LIVE=1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "model",
    [
        "qwen-embedding",
        "reranker",
        "whisper",
        "speech",
        "tts",
        "chatterbox",
        "auk",
        "image",
        "video",
        "content-safety",
        "llama-guard",
        "stable-diffusion",
        "flux",
    ],
)
def test_skips_non_chat_models(model):
    assert probe.select_models([{"id": model}, {"id": "chat-model"}], None) == ["chat-model"]
    assert probe.select_models([{"id": model}], model) == [model]


def test_candidates_use_catalog_metadata():
    catalog = [
        {"id": "opaque-a", "architecture": {"output_modalities": ["image"]}},
        {"id": "opaque-b", "task": "text-to-speech"},
        {"id": "chat-model", "features": ["tools", "response_format"]},
    ]
    assert probe.select_models(catalog, None) == ["chat-model"]
    assert probe.select_models(catalog, " opaque-b,chat-model,opaque-b ") == ["opaque-b", "chat-model"]
    with pytest.raises(ValueError):
        probe.select_models(catalog, "missing")


@pytest.mark.parametrize("custom_out", [False, True])
def test_main_mock_catalog_and_adapter(monkeypatch, tmp_path, capsys, custom_out):
    monkeypatch.setenv("AI_LIVE", "1")
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "synthetic-probe-key")
    monkeypatch.setattr(probe, "DEFAULT_REPORT_DIR", tmp_path / "default")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-probe-key"
        assert request.extensions["timeout"]["read"] == 60
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "chat-a", "owned_by": "example", "features": ["synthetic-probe-key"], "price": 0.2},
                        {"id": "whisper"},
                    ]
                },
            )
        body = json.loads(request.content)
        assert body["model"] == "chat-a"
        if body["response_format"]["type"] == "json_schema":
            schema = body["response_format"]["json_schema"]
            assert schema["strict"] is True
            assert schema["schema"]["required"] == ["kind", "message_to_staff"]
        else:
            assert "Return JSON matching this schema" in body["messages"][0]["content"]
        content = TOOL if "list_services" in body["messages"][0]["content"] else FINAL
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            },
        )

    def fake_client(**kwargs):
        assert kwargs["allowlist"]
        return httpx.Client(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(probe, "client", fake_client)

    def no_dotenv(*args, **kwargs):
        pytest.fail("Probe must not load application settings/.env")

    monkeypatch.setattr("app.platform.config.get_settings", no_dotenv)
    args = ["--provider", "vultr", "--base-url", "https://catalog.example.com/v1"]
    if custom_out:
        args += ["--out", str(tmp_path), "--models", "chat-a"]
    assert probe.main(args) == 0
    assert len(requests) == 4 and str(requests[0].url) == "https://catalog.example.com/v1/models"
    assert all(str(r.url).startswith("https://catalog.example.com/v1/") for r in requests)
    captured = capsys.readouterr()
    report_dir = tmp_path if custom_out else tmp_path / "default"
    artifacts = "".join(p.read_text() for p in report_dir.glob("*-probe.*"))
    assert "synthetic-probe-key" not in captured.out + captured.err + artifacts
    assert '"owned_by": "example"' in captured.out and '"price": 0.2' in captured.out
    assert len(json.loads(next(report_dir.glob("*-probe.json")).read_text())["results"]) == 3


def test_main_missing_key(monkeypatch, capsys):
    monkeypatch.setenv("AI_LIVE", "1")
    monkeypatch.delenv("VULTR_INFERENCE_API_KEY", raising=False)
    assert probe.main([]) == 2
    assert "key" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("Authorization: secret"), RuntimeError("secret headers")])
def test_catalog_errors_are_safe(monkeypatch, capsys, failure):
    monkeypatch.setenv("AI_LIVE", "1")
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "synthetic-key")

    def fail(request):
        raise failure

    monkeypatch.setattr(probe, "client", lambda **kwargs: httpx.Client(transport=httpx.MockTransport(fail)))
    assert probe.main([]) == 2
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err
    assert "Authorization" not in output.out + output.err


@pytest.mark.parametrize("status", [401, 429, 500, 200])
def test_catalog_bad_response_is_safe(monkeypatch, tmp_path, capsys, status):
    monkeypatch.setenv("AI_LIVE", "1")
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "synthetic-key")
    monkeypatch.setattr(
        probe,
        "client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json={"error": "Authorization: synthetic-key"})
            )
        ),
    )
    assert probe.main(["--out", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "synthetic-key" not in output.out + output.err
    assert "Authorization" not in output.out + output.err
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("text", ["not JSON", '{"kind":"final"}', '{"kind":"final","message_to_staff":42}'])
def test_invalid_output_records_failure_and_continues(text):
    gateway = FakeGateway([text, FINAL, '{"kind":"tool_call","tool":"list_services","arguments":{}}'])
    rows = probe.probe_models(gateway, ["chat-a"])
    assert [row.schema_valid for row in rows] == [False, True, False]
    assert rows[0].error_kind == rows[2].error_kind == "validation_error"
    assert len(gateway.calls) == 3
