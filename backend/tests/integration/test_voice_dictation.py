import builtins
import io
import json
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.main import create_app
from app.modules.ai import public as ai
from app.modules.ai.gateway.fake import FakeGateway
from app.modules.ai.gateway.router import Router, set_router_for_tests
from app.modules.ai.gateway.routes import ProviderCfg, RouteEntry, RoutesFile
from app.modules.ai.gateway.types import ChatResult, ProviderError, Usage
from app.modules.org.testing import make_business
from app.modules.voice import lifecycle, service
from app.modules.voice.models import VoiceSession
from app.modules.voice.upload import MAX_AUDIO, valid_magic
from app.platform import clock
from app.platform.config import get_settings
from app.platform.db import transaction
from app.platform.registry import ErasureTarget
from app.platform.testing.auth import client_for, ctx_for

BASE = "/api/v1/voice/transcriptions"
WAV = b"RIFF" + b"\x10\x00\x00\x00" + b"WAVEfmt " + b"\0" * 32
RAW = " um order twelve no ten brake pads "
CLEAN = "Order 10 brake pads."


@pytest.fixture
def business():
    return make_business()


@pytest.fixture
def other_business():
    return make_business("example-garden-store")


@pytest.fixture(autouse=True)
def clean_cache():
    service._cache.clear()
    yield
    set_router_for_tests(None)
    service._cache.clear()


class Speech(FakeGateway):
    def __init__(self, raw=RAW, seconds=8, failure=None, callback=None):
        super().__init__([])
        self.raw, self.seconds, self.failure, self.callback = raw, seconds, failure, callback
        self.audio_calls = []

    def transcribe(self, req):
        self.audio_calls.append(req)
        if self.callback:
            self.callback()
        if self.failure:
            raise self.failure
        return ChatResult(
            text=self.raw,
            provider=self.name,
            model=req.model,
            latency_ms=3,
            usage=Usage(input_tokens=0, output_tokens=0, audio_seconds=self.seconds),
        )


def voice_router(speech=None, output=None, *, hosted=True):
    speech = speech or Speech()
    writer = FakeGateway([output if output is not None else json.dumps({"text": CLEAN})])
    providers = {
        "speech": ProviderCfg(
            kind="openai_compat",
            vendor="vultr" if hosted else "local",
            hosted=hosted,
            base_url="https://api.example-provider.test/v1" if hosted else "http://127.0.0.1:8080/v1",
        ),
        "writer": ProviderCfg(kind="fake", vendor="vultr"),
    }
    routes = RoutesFile(
        providers=providers,
        routes={
            "stt": [RouteEntry(provider="speech", model="whisper", price_per_audio_minute=0.006)],
            "writer": [RouteEntry(provider="writer", model="editor", price_in_per_m=0.2, price_out_per_m=0.2)],
        },
    )
    router = Router(routes, {"speech": speech, "writer": writer}, resolver=lambda host: ["127.0.0.1"])
    set_router_for_tests(router)
    return router, speech, writer


def upload(client, data=WAV, mime="audio/wav", **kwargs):
    return client.post(BASE, files={"audio": ("recording.wav", data, mime)}, **kwargs)


def client(registry, business):
    return client_for(create_app(registry), registry, business, {"ai.assistant.use"})


def test_transcription_requires_confirmation_before_message_created(registry, business, owner_engine):
    _, speech, writer = voice_router()
    response = upload(client(registry, business))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "text": CLEAN,
        "raw_text": RAW.strip(),
        "duration_s": 8,
        "polished": True,
        "warnings": [],
    }
    with owner_engine.connect() as db:
        for table in ("messages", "assistant_messages", "action_proposals", "outbox_messages"):
            assert db.scalar(text(f"select count(*) from {table}")) == 0  # noqa: S608
        assert db.scalar(text("select count(*) from voice_sessions")) == 1
    assert len(speech.audio_calls) == len(writer.calls) == 1


@pytest.mark.parametrize("fail", [False, True])
def test_audio_in_memory_on_success_and_error(registry, business, monkeypatch, fail):
    voice_router(Speech(failure=ProviderError("auth") if fail else None))
    http = client(registry, business)

    def no_disk(*args, **kwargs):
        pytest.fail("dictation attempted filesystem access")

    real_io_open = io.open

    def no_write(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            pytest.fail("dictation wrote to disk")
        return real_io_open(file, mode, *args, **kwargs)

    # Block every temp-file path and writes through both normal file APIs.
    monkeypatch.setattr(tempfile, "TemporaryFile", no_disk)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", no_disk)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", no_disk)
    monkeypatch.setattr(builtins, "open", no_write)
    monkeypatch.setattr(io, "open", no_write)
    result = upload(http, data=WAV + b"\0" * (2 * 1024 * 1024))
    assert result.status_code == (503 if fail else 200), result.text


def test_size_cap_before_provider_call(registry, business):
    _, speech, _ = voice_router()
    response = upload(client(registry, business), data=WAV + b"\0" * MAX_AUDIO)
    assert response.status_code == 413
    assert not speech.audio_calls


@pytest.mark.parametrize("mime", ["audio/wav", "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "text/plain"])
def test_magic_byte_check_before_provider_call(registry, business, mime):
    _, speech, _ = voice_router()
    assert upload(client(registry, business), data=b"pretend audio", mime=mime).status_code == 422
    assert not speech.audio_calls


@pytest.mark.parametrize(
    ("mime", "data"),
    [
        ("audio/wav", WAV),
        ("audio/x-wav", WAV),
        ("audio/webm", b"\x1a\x45\xdf\xa3\x00webm\x00Opus"),
        ("audio/ogg", b"OggS" + b"\0" * 25 + b"OpusHead"),
        ("audio/mp4", b"\x00\x00\x00\x20ftypM4A "),
        ("audio/mpeg", b"ID3\x04\x00"),
    ],
)
def test_supported_magic(mime, data):
    assert valid_magic(data, mime)


def test_provider_duration_over_125_rejected(registry, business, owner_engine):
    _, _, writer = voice_router(Speech(seconds=125.01))
    assert upload(client(registry, business)).status_code == 422
    assert not writer.calls
    with owner_engine.connect() as db:
        assert db.scalar(text("select status from voice_sessions")) == "failed"


@pytest.mark.parametrize(("policy", "regulated"), [("strict_private", False), ("hosted_vultr", True)])
def test_voice_disabled_under_strict_private_without_local_stt(
    registry, business, owner_engine, monkeypatch, policy, regulated
):
    _, speech, _ = voice_router()
    with owner_engine.begin() as db:
        db.execute(
            text("update business_settings set settings=jsonb_set(settings,'{ai_policy}',to_jsonb(cast(:p as text)))"),
            {"p": policy},
        )
    if regulated:
        monkeypatch.setattr(get_settings(), "data_profile", "regulated")
    try:
        response = upload(client(registry, business))
        assert response.status_code == 409 and response.json()["code"] == "voice.unavailable"
        assert not speech.audio_calls
        with transaction(ctx_for(business, {"ai.assistant.use"})) as db:
            assert not ai.status(db, ctx_for(business, {"ai.assistant.use"})).model_classes["stt"].enabled
    finally:
        pass


def test_local_stt_allowed_for_strict_private(registry, business, owner_engine):
    voice_router(hosted=False)
    with owner_engine.begin() as db:
        db.execute(text('update business_settings set settings=\'{"ai_policy":"strict_private"}\''))
    response = upload(client(registry, business))
    assert response.status_code == 200 and response.json()["polished"] is True


def test_ai_off_disables_voice_before_inference(registry, business, owner_engine):
    _, speech, _ = voice_router()
    with owner_engine.begin() as db:
        db.execute(
            text("update business_settings set settings=cast(:s as jsonb)"), {"s": '{"ai_daily_token_budget":0}'}
        )
    response = upload(client(registry, business))
    assert response.status_code == 409 and response.json()["reason"] == "ai.off"
    assert not speech.audio_calls


@pytest.mark.parametrize(
    ("output", "warning"),
    [
        ('{"text":"Order 11 brake pads."}', "voice.polish_new_number"),
        ('{"text":"Zelda order 10 brake pads."}', "voice.polish_new_name"),
        ('{"text":"order order order order order order order order order order"}', "voice.polish_too_long"),
        (ProviderError("timeout"), "voice.polish_failed"),
        ("not valid json", "voice.polish_failed"),
    ],
)
def test_polish_rejection_returns_raw(registry, business, output, warning):
    voice_router(output=output)
    response = upload(client(registry, business))
    assert response.status_code == 200, response.text
    assert response.json()["text"] == RAW.strip()
    assert not response.json()["polished"] and response.json()["warnings"] == [warning]


def test_polish_budget_exhaustion_returns_raw(business, monkeypatch):
    from app.modules.ai import dictation
    from app.modules.ai.errors import AiError

    router, _, _ = voice_router()

    def exhausted(*args):
        raise AiError("ai.budget_exceeded")

    monkeypatch.setattr(router, "complete", exhausted)
    assert dictation.polish(ctx_for(business, {"ai.assistant.use"}), RAW, [], router) == (
        RAW.strip(),
        False,
        ["voice.polish_budget_exhausted"],
    )


def test_permission_required(registry, business):
    voice_router()
    http = client_for(create_app(registry), registry, business, set())
    assert upload(http).status_code == 403


def test_rate_limit_and_idempotency(registry, business, owner_engine):
    _, speech, _ = voice_router()
    http = client(registry, business)
    headers = {"Idempotency-Key": "voice-1"}
    first = upload(http, headers=headers)
    assert first.status_code == 200, first.text
    assert upload(http, headers=headers).json() == first.json()
    assert len(speech.audio_calls) == 1
    assert upload(http, data=WAV + b"x", headers=headers).status_code == 409
    assert upload(http, headers={"Idempotency-Key": "x" * 65}).status_code == 422
    service._cache.clear()
    retry = upload(http, headers=headers)
    assert retry.status_code == 409 and retry.json()["code"] == "voice.replay_unavailable"
    with owner_engine.begin() as db:
        db.execute(text("update rate_limits set count=30 where key like 'voice:%'"))
    assert upload(http).status_code == 429
    assert len(speech.audio_calls) == 1


def test_concurrent_idempotency_claim(registry, business):
    entered, release = threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert release.wait(5)

    _, speech, _ = voice_router(Speech(callback=wait))
    http = client(registry, business)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(upload, http, headers={"Idempotency-Key": "simultaneous"})
        try:
            assert entered.wait(5)
            second = upload(http, headers={"Idempotency-Key": "simultaneous"})
            assert second.status_code == 409 and second.json()["code"] == "voice.operation_in_progress"
        finally:
            release.set()
        assert future.result().status_code == 200
    assert len(speech.audio_calls) == 1


def test_recordings_and_export_never_contain_transcript(registry, business, owner_engine):
    voice_router()
    assert upload(client(registry, business)).status_code == 200
    with owner_engine.connect() as db:
        for table in ("ai_runs", "ai_run_steps", "voice_sessions", "idempotency_keys"):
            rows = db.execute(text(f"select row_to_json(t) from {table} t")).scalars().all()  # noqa: S608
            serialized = json.dumps(rows, default=str)
            assert "brake pads" not in serialized and "twelve" not in serialized
        assert db.scalar(text("select use_case from ai_runs")) == "dictation"
        assert db.scalar(text("select result from ai_runs")) == {"polished": True}
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        exported = lifecycle.export(db, ctx)
        assert list(exported) == ["voice_sessions"]
        assert "raw_text" not in json.dumps(exported, default=str)


def test_metadata_retention_export_erasure_and_rls(registry, business, other_business, owner_engine):
    voice_router()
    assert upload(client(registry, business), headers={"Idempotency-Key": "keep"}).status_code == 200
    assert upload(client(registry, other_business)).status_code == 200
    ctx = ctx_for(business)
    with transaction(ctx) as db:
        rows = list(db.scalars(select(VoiceSession)))
        assert len(rows) == 1 and rows[0].business_id == business
        uid = rows[0].user_id
        assert lifecycle.erase(db, ctx, ErasureTarget(uid, "Example", (), ())) == {"voice_sessions_deleted": 1}
    with owner_engine.begin() as db:
        db.execute(text("update voice_sessions set created_at=:old"), {"old": clock.now() - timedelta(days=91)})
    with transaction(ctx_for(other_business)) as db:
        lifecycle.cleanup(db, ctx_for(other_business), {})
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from voice_sessions")) == 0
    assert not service._cache


def test_dictionary_permission_and_business_scope(business, other_business, owner_engine):
    from app.modules.ai.dictation_dictionary import dictionary
    from app.modules.catalog.models import CatalogItem
    from app.modules.crm.models import Customer
    from app.modules.identity.models import Membership, User
    from app.modules.purchasing.models import Supplier

    customers = {}
    for b, label in [(business, "Visible"), (other_business, "Foreign")]:
        with transaction(ctx_for(b)) as db:
            user = User(
                email=f"{label.lower()}@example.com", display_name=f"{label} Staff", oidc_subject=f"voice-{label}"
            )
            db.add(user)
            db.flush()
            db.add(Membership(business_id=b, user_id=user.id, status="active"))
            db.add(Supplier(business_id=b, name=f"{label} Supplies"))
            db.add(
                CatalogItem(
                    business_id=b,
                    name=f"{label} Pads",
                    sku=f"{label.upper()}-10",
                    kind="product",
                    unit="each",
                    currency="USD",
                    price_minor=100,
                )
            )
            customer = Customer(business_id=b, kind="person", source="staff", display_name=f"{label} Customer")
            db.add(customer)
            db.flush()
            customers[b] = customer.id
    ids = list(customers.values())
    with transaction(ctx_for(business)) as db:
        assert dictionary(db, ctx_for(business), ids) == []
    perms = {"catalog.item.read", "purchasing.po.read", "identity.membership.read", "crm.customer.read"}
    ctx = ctx_for(business, perms)
    with transaction(ctx) as db:
        terms = dictionary(db, ctx, ids)
        assert {"Visible Pads", "VISIBLE-10", "Visible Supplies", "Visible Staff", "Visible Customer"} <= set(terms)
        assert not any("Foreign" in term or "FOREIGN" in term for term in terms)
        assert "Visible Customer" not in dictionary(db, ctx, [])
        assert len(terms) <= 200


@pytest.mark.parametrize(
    "fields",
    [
        {"context": "unknown"},
        {"entity_ids": "not-uuid"},
        [("entity_ids", str(uuid.uuid4())) for _ in range(11)],
    ],
)
def test_invalid_context_or_entities_before_provider(registry, business, fields):
    _, speech, _ = voice_router()
    if isinstance(fields, list):
        # HTTPX list-valued form fields use a dict with a list for repeat encoding.
        fields = {"entity_ids": [value for _, value in fields]}
    response = client(registry, business).post(BASE, files={"audio": ("voice.wav", WAV, "audio/wav")}, data=fields)
    assert response.status_code == 422
    assert not speech.audio_calls


def test_status_disables_stt_when_ai_off(registry, business, owner_engine):
    voice_router()
    with owner_engine.begin() as db:
        db.execute(
            text("update business_settings set settings=cast(:s as jsonb)"), {"s": '{"ai_daily_token_budget":0}'}
        )
    ctx = ctx_for(business, {"ai.assistant.use"})
    with transaction(ctx) as db:
        assert not ai.status(db, ctx).model_classes["stt"].enabled


def test_existing_retention_job_cleans_voice_metadata(registry, business, owner_engine):
    from app.modules.ai.jobs import assistant_retention_job

    voice_router()
    assert upload(client(registry, business)).status_code == 200
    with owner_engine.begin() as db:
        db.execute(text("update voice_sessions set created_at=:old"), {"old": clock.now() - timedelta(days=91)})
    assistant_retention_job(ctx_for(business, actor="system"), {})
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from voice_sessions")) == 0


def test_interrupted_audio_upload_keeps_all_bytes_in_memory(monkeypatch):
    import asyncio
    import tempfile

    from starlette.requests import ClientDisconnect, Request

    from app.modules.voice.upload import read_audio

    chunks = iter(
        [
            {
                "type": "http.request",
                "body": b'--v\r\nContent-Disposition: form-data; name="audio"; filename="v.wav"\r\n'
                b"Content-Type: audio/wav\r\n\r\n" + WAV,
                "more_body": True,
            },
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(chunks)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"multipart/form-data; boundary=v")],
        },
        receive,
    )

    def no_temp(*args, **kwargs):
        pytest.fail("interrupted upload used disk")

    monkeypatch.setattr(tempfile, "TemporaryFile", no_temp)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", no_temp)
    with pytest.raises(ClientDisconnect):
        asyncio.run(read_audio(request))


def test_exact_audio_size_cap_is_accepted(registry, business):
    _, speech, _ = voice_router()
    response = upload(client(registry, business), data=WAV + b"\0" * (MAX_AUDIO - len(WAV)))
    assert response.status_code == 200, response.text
    assert len(speech.audio_calls) == 1
