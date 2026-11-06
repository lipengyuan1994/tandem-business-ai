from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

from app.modules.ai import reservations
from app.modules.ai.gateway.openai_compat import OpenAICompatAdapter
from app.modules.ai.gateway.router import PolicyBlocked, Router, set_router_for_tests
from app.modules.ai.gateway.routes import RouteEntry, RoutesFile
from app.modules.ai.gateway.types import ProviderError, TranscriptionRequest
from app.modules.ai.model_calls import budget_scope, policy_scope
from app.modules.org.testing import make_business
from app.platform.config import get_settings
from app.platform.testing.auth import ctx_for
from tests.integration.test_voice_dictation import WAV, Speech, voice_router


@pytest.fixture(autouse=True)
def clear_router_override():
    set_router_for_tests(None)
    yield
    set_router_for_tests(None)


def request(**kwargs):
    return TranscriptionRequest(audio=WAV, filename="private-name.wav", mime="audio/wav", **kwargs)


def test_adapter_multipart_json_language_duration_and_safe_filename():
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={"text": " um ten pads ", "usage": {"type": "duration", "seconds": 8}})

    adapter = OpenAICompatAdapter("vultr", "https://api.example-provider.test/v1", None)
    adapter._client = httpx.Client(transport=httpx.MockTransport(respond))
    result = adapter.transcribe(request(model="whisper-large-v3-turbo", language="en"))
    assert result.usage.audio_seconds == 8 and result.text == " um ten pads "
    assert calls[0].url.path == "/v1/audio/transcriptions"
    body = calls[0].read()
    for marker in (b"whisper-large-v3-turbo", b'name="file"', b'name="language"', b"\r\njson\r\n"):
        assert marker in body
    assert b"verbose_json" not in body and b"private-name" not in body


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, "bad_request"), (401, "auth"), (403, "auth"), (429, "rate_limited"), (500, "server_error")],
)
def test_adapter_error_mapping(status, kind):
    adapter = OpenAICompatAdapter("vultr", "https://api.example-provider.test/v1", None)
    adapter._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, headers={"Retry-After": "5"}, json={"text": "never log this"})
        )
    )
    with pytest.raises(ProviderError) as exc:
        adapter.transcribe(request())
    assert exc.value.kind == kind and "never log" not in str(exc.value)
    if status == 429:
        assert exc.value.retry_after_s == 5


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "hello"},
        {"text": "hello", "usage": {"seconds": -1}},
        {"text": "hello", "usage": {"seconds": True}},
        {"text": [], "duration": 1},
    ],
)
def test_missing_or_invalid_duration_is_not_free(payload):
    adapter = OpenAICompatAdapter("local", "http://whisper.local/v1", None, local=True)
    adapter._client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    with pytest.raises(ProviderError):
        adapter.transcribe(request())


def test_local_compatible_duration():
    adapter = OpenAICompatAdapter("local", "http://whisper.local/v1", None, local=True)
    adapter._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: (
                httpx.Response(200, json={"text": "hello", "duration": 3.5})
                if b"verbose_json" in req.read()
                else httpx.Response(400)
            )
        )
    )
    assert adapter.transcribe(request()).usage.audio_seconds == 3.5


@pytest.mark.parametrize("failure", [None, ProviderError("timeout")])
def test_audio_budget_reserve_120_settle_or_retain(owner_engine, failure):
    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use"})
    observed = []

    def inspect_reserved():
        with owner_engine.connect() as db:
            observed.append(
                db.scalar(text("select cost_micros from ai_usage_daily where business_id=:b"), {"b": business})
            )

    speech = Speech(seconds=8, failure=failure, callback=inspect_reserved)
    router, _, _ = voice_router(speech)
    with budget_scope(ctx):
        if failure:
            with pytest.raises(ProviderError):
                router.transcribe("hosted_vultr", request())
        else:
            router.transcribe("hosted_vultr", request())
    assert observed == [12000]
    with owner_engine.connect() as db:
        usage = db.execute(text("select cost_micros,input_tokens,output_tokens from ai_usage_daily")).one()
        assert usage == (12000 if failure else 800, 0, 0)
        assert set(db.execute(text("select cost_micros from ai_cost_periods")).scalars()) == {usage[0]}


def test_audio_budget_denial_calls_no_provider(monkeypatch):
    from app.modules.ai.errors import AiError

    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use"})
    router, speech, _ = voice_router()
    monkeypatch.setattr(get_settings(), "ai_daily_cost_limit_usd", Decimal("0.001"))
    with budget_scope(ctx), pytest.raises(AiError, match=r"ai\.budget_exceeded"):
        router.transcribe("hosted_vultr", request())
    assert not speech.audio_calls


def test_recheck_policy_after_budget_lock_before_stt(monkeypatch):
    business = make_business()
    ctx = ctx_for(business, {"ai.assistant.use"})
    router, speech, _ = voice_router()
    state = ["hosted_vultr"]
    reserve = reservations.reserve

    def tighten(*args):
        value = reserve(*args)
        state[0] = "strict_private"
        return value

    monkeypatch.setattr(reservations, "reserve", tighten)
    with budget_scope(ctx), policy_scope(lambda: state[0]), pytest.raises(PolicyBlocked):
        router.transcribe("hosted_vultr", request())
    assert not speech.audio_calls


def test_transient_fallback_and_single_deadline():
    from app.modules.ai.gateway.routes import ProviderCfg

    first = Speech(failure=ProviderError("timeout"))
    second = Speech()
    providers = {p: ProviderCfg(kind="openai_compat", vendor="vultr") for p in ("one", "two")}
    config = RoutesFile(
        providers=providers,
        routes={"stt": [RouteEntry(provider=p, model=p, price_per_audio_minute=0.006) for p in providers]},
    )
    router = Router(config, {"one": first, "two": second})
    assert router.transcribe("hosted_vultr", request())[1].provider == "two"
    assert second.audio_calls[0].timeout_s <= first.audio_calls[0].timeout_s
    first.failure = ProviderError("auth")
    with pytest.raises(ProviderError, match="auth"):
        router.transcribe("hosted_vultr", request())
    assert len(second.audio_calls) == 1


def test_stt_local_egress_and_allowlist(monkeypatch):
    adapter = OpenAICompatAdapter("local", "http://127.0.0.1:8080/v1", None, local=True)
    monkeypatch.setattr(get_settings(), "local_inference_hosts", "")
    with pytest.raises(ProviderError, match="egress denied"):
        adapter.transcribe(request())


def test_audio_reservation_rounds_up_fractional_microdollars():
    from app.modules.ai.gateway.pricing import cost_micros
    from app.modules.ai.gateway.types import Usage

    entry = RouteEntry(provider="local", model="whisper", price_per_audio_minute=0.0000001)
    usage = Usage(input_tokens=0, output_tokens=0, audio_seconds=120)
    assert cost_micros(entry, usage, round_up=True) == 1
