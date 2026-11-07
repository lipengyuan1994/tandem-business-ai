"""Bounded in-memory multipart PDF upload; no UploadFile spooling or temporary PDF on disk."""

from typing import Any

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from app.platform.errors import DomainError, Invalid

MAX_PDF = 10 * 1024 * 1024


class TooLarge(DomainError):
    status = 413
    title = "PDF too large"


async def read_pdf(request: Request) -> tuple[bytes, str]:
    kind, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary", b"")
    if kind != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise Invalid("knowledge.invalid_multipart")
    part = bytearray()
    header_name, header_value = bytearray(), bytearray()
    headers: dict[bytes, bytes] = {}
    parts = 0
    size = 0
    ended = False
    name = "document.pdf"
    mime = ""

    def begin() -> None:
        nonlocal parts
        parts += 1
        if parts != 1:
            raise Invalid("knowledge.single_pdf_required")

    def field(data: bytes, start: int, end: int) -> None:
        nonlocal size
        size += end - start
        if size > 4096:
            raise Invalid("knowledge.invalid_multipart")
        header_name.extend(data[start:end])

    def value(data: bytes, start: int, end: int) -> None:
        nonlocal size
        size += end - start
        if size > 4096:
            raise Invalid("knowledge.invalid_multipart")
        header_value.extend(data[start:end])

    def header_end() -> None:
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_done() -> None:
        nonlocal name, mime
        _, attrs = parse_options_header(headers.get(b"content-disposition", b""))
        if attrs.get(b"name") != b"file" or b"filename" not in attrs:
            raise Invalid("knowledge.single_pdf_required")
        name = attrs[b"filename"].decode("utf-8", errors="replace")
        mime = headers.get(b"content-type", b"").decode("ascii", errors="replace").split(";")[0].lower().strip()
        if mime != "application/pdf":
            raise Invalid("knowledge.invalid_pdf")

    def data(data: bytes, start: int, end: int) -> None:
        if len(part) + end - start > MAX_PDF:
            raise TooLarge("knowledge.pdf_too_large")
        part.extend(data[start:end])

    def end() -> None:
        nonlocal ended
        ended = True

    callbacks: dict[str, Any] = {
        "on_part_begin": begin,
        "on_header_field": field,
        "on_header_value": value,
        "on_header_end": header_end,
        "on_headers_finished": headers_done,
        "on_part_data": data,
        "on_end": end,
    }
    parser = MultipartParser(boundary, callbacks)  # type: ignore[arg-type]
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_PDF + 16384:
                raise TooLarge("knowledge.pdf_too_large")
            parser.write(chunk)
        parser.finalize()
    except MultipartParseError:
        raise Invalid("knowledge.invalid_multipart") from None
    if not ended or parts != 1 or mime != "application/pdf" or not part.startswith(b"%PDF-"):
        raise Invalid("knowledge.invalid_pdf")
    return bytes(part), name
