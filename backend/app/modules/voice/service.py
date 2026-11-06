"""Metadata-only durable claims plus short-lived in-memory replay. No sends or message writes."""

import hashlib
import threading
import time
import uuid
from collections import OrderedDict

from sqlalchemy import select, text

from app.modules.ai import public as ai
from app.platform import ratelimit
from app.platform.canonical import sha256_hex
from app.platform.context import Context
from app.platform.db import transaction
from app.platform.errors import Conflict

from .models import VoiceSession
from .upload import Audio

_cache: OrderedDict[uuid.UUID, tuple[float, ai.DictationOut]] = OrderedDict()
_lock = threading.Lock()


def forget(session_ids: list[uuid.UUID]) -> None:
    with _lock:
        for key in session_ids:
            _cache.pop(key, None)


def _cached(session_id: uuid.UUID) -> ai.DictationOut | None:
    with _lock:
        now = time.monotonic()
        for key in list(_cache):
            if _cache[key][0] < now:
                del _cache[key]
        row = _cache.get(session_id)
        return row[1] if row else None


def transcribe(ctx: Context, upload: Audio, key: str | None) -> ai.DictationOut:
    ctx.require("ai.assistant.use")
    ai.voice_available(ctx)
    digest = sha256_hex(
        {
            "audio": hashlib.sha256(upload.data).hexdigest(),
            "mime": upload.mime,
            "context": upload.fields.context,
            "entity_ids": sorted(map(str, upload.fields.entity_ids)),
            "permissions": sorted(ctx.permissions),
        }
    )
    with transaction(ctx) as db:
        if key is not None:
            db.execute(
                text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"voice:{ctx.business_id}:{ctx.user_id}:{key}"},
            )
            old = db.scalar(
                select(VoiceSession).where(
                    VoiceSession.business_id == ctx.business_id,
                    VoiceSession.user_id == ctx.user_id,
                    VoiceSession.operation_key == key,
                )
            )
            if old is not None:
                if old.request_hash != digest:
                    raise Conflict("ai.idempotency_key_reused")
                cached = _cached(old.id)
                if old.status == "succeeded" and cached:
                    return cached
                raise Conflict("voice.operation_in_progress" if old.status == "active" else "voice.replay_unavailable")
        ratelimit.hit(db, f"voice:dictation:{ctx.user_id}", 30, 3600)
        terms = ai.dictation_dictionary(db, ctx, upload.fields.entity_ids)
        new_row = VoiceSession(
            business_id=ctx.business_id,
            user_id=ctx.user_id,
            context=upload.fields.context,
            latency_ms=0,
            status="active",
            operation_key=key,
            request_hash=digest,
        )
        db.add(new_row)
        db.flush()
        session_id = new_row.id
    started = time.monotonic()
    try:
        value, provider_result = ai.transcribe_dictation(
            ctx, upload.data, upload.filename, upload.mime, terms, session_id
        )
        with transaction(ctx) as db:
            row = db.get(VoiceSession, session_id)
            assert row is not None
            row.status = "succeeded"
            row.duration_ms = round(value.duration_s * 1000)
            row.provider, row.model = provider_result.provider, provider_result.model
            row.latency_ms = round((time.monotonic() - started) * 1000)
        if key is not None:
            with _lock:
                _cache[session_id] = (time.monotonic() + 600, value)
                while len(_cache) > 128:
                    _cache.popitem(last=False)
            # Expire even when there are no further requests. The timer retains only the UUID, never the text.
            expiry = threading.Timer(600, forget, args=([session_id],))
            expiry.daemon = True
            expiry.start()
        return value
    except Exception as exc:
        with transaction(ctx) as db:
            row = db.get(VoiceSession, session_id)
            if row is not None:
                row.status, row.error_code = "failed", getattr(exc, "code", "voice.failed")
                metadata = getattr(exc, "extra", {})
                if row.error_code == "voice.duration_exceeded":
                    row.duration_ms = round(metadata["duration_s"] * 1000)
                    row.provider, row.model = metadata["provider"], metadata["model"]
                row.latency_ms = round((time.monotonic() - started) * 1000)
        raise
