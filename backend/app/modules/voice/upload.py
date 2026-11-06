"""Streaming multipart parser with bounded memory; never creates UploadFile or a temporary file."""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, Field, ValidationError
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from app.platform.errors import DomainError, Invalid

MAX_AUDIO = 4 * 1024 * 1024
MAX_BODY = MAX_AUDIO + 16 * 1024
VoiceContext = Literal["assistant", "inbox_reply", "purchasing", "inventory", "knowledge", "note", "other"]


class TooLarge(DomainError):
    status = 413
    title = "Audio too large"


class Fields(BaseModel):
    context: VoiceContext = "other"
    entity_ids: list[UUID] = Field(default_factory=list, max_length=10)


@dataclass(frozen=True)
class Audio:
    data: bytes
    mime: str
    filename: str
    fields: Fields


def valid_magic(audio: bytes, mime: str) -> bool:
    if mime in {"audio/wav", "audio/x-wav"}:
        return audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"
    if mime == "audio/webm":
        return audio.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in audio[:4096]
    if mime == "audio/ogg":
        return audio.startswith(b"OggS") and any(tag in audio[:4096] for tag in (b"OpusHead", b"vorbis"))
    if mime == "audio/mp4":
        return audio[4:8] == b"ftyp" and len(audio) >= 12
    if mime == "audio/mpeg":
        return audio.startswith(b"ID3") or (len(audio) > 1 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0)
    return False


async def read_audio(request: Request) -> Audio:
    kind, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary", b"")
    if kind != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise Invalid("voice.invalid_multipart")
    header_bytes = bytearray()
    header_name = bytearray()
    headers: dict[bytes, bytes] = {}
    part = bytearray()
    values: dict[str, list[str]] = {}
    seen: set[str] = set()
    audio: bytes | None = None
    mime = ""
    field_name = ""
    ended = False
    header_size = 0
    parts = 0

    def begin() -> None:
        nonlocal header_size, parts
        headers.clear()
        part.clear()
        header_size = 0
        parts += 1
        if parts > 13:
            raise Invalid("voice.too_many_fields")

    def header_field(data: bytes, start: int, end: int) -> None:
        nonlocal header_size
        header_size += end - start
        if header_size > 4096:
            raise Invalid("voice.invalid_multipart")
        header_name.extend(data[start:end])

    def header_value(data: bytes, start: int, end: int) -> None:
        nonlocal header_size
        header_size += end - start
        if header_size > 4096:
            raise Invalid("voice.invalid_multipart")
        header_bytes.extend(data[start:end])

    def header_end() -> None:
        headers[bytes(header_name).lower()] = bytes(header_bytes)
        header_name.clear()
        header_bytes.clear()

    def headers_done() -> None:
        nonlocal field_name, mime
        _, attrs = parse_options_header(headers.get(b"content-disposition", b""))
        try:
            field_name = attrs.get(b"name", b"").decode("ascii")
        except UnicodeError:
            raise Invalid("voice.invalid_multipart") from None
        if field_name not in {"audio", "context", "entity_ids"}:
            raise Invalid("voice.invalid_field")
        if field_name != "entity_ids" and field_name in seen:
            raise Invalid("voice.duplicate_field")
        seen.add(field_name)
        if field_name == "audio":
            mime = headers.get(b"content-type", b"").decode("ascii", errors="replace").split(";")[0].strip().lower()

    def data(data: bytes, start: int, end: int) -> None:
        cap = MAX_AUDIO if field_name == "audio" else 1024
        if len(part) + end - start > cap:
            raise TooLarge("voice.audio_too_large")
        part.extend(data[start:end])

    def part_end() -> None:
        nonlocal audio
        if field_name == "audio":
            audio = bytes(part)
        else:
            try:
                values.setdefault(field_name, []).append(part.decode("utf-8"))
            except UnicodeError:
                raise Invalid("voice.invalid_field") from None

    def end() -> None:
        nonlocal ended
        ended = True

    callbacks: dict[str, Any] = {
        "on_part_begin": begin,
        "on_header_field": header_field,
        "on_header_value": header_value,
        "on_header_end": header_end,
        "on_headers_finished": headers_done,
        "on_part_data": data,
        "on_part_end": part_end,
        "on_end": end,
    }
    parser = MultipartParser(boundary, callbacks)  # type: ignore[arg-type]
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY:
                raise TooLarge("voice.audio_too_large")
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError:
        raise Invalid("voice.invalid_multipart") from None
    if not ended or not audio or not valid_magic(audio, mime):
        raise Invalid("voice.invalid_audio")
    try:
        fields = Fields(context=values.get("context", ["other"])[0], entity_ids=values.get("entity_ids", []))
    except ValidationError:
        raise Invalid("voice.invalid_field") from None
    # Never forward the client's filename (could contain private text or header escapes).
    return Audio(audio, mime, "dictation.audio", fields)
