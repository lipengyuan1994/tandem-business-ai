"""Data export helpers (M18): every module dumps its own tables for one business; admin_ops zips the result."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Buffer, Generator, Iterable, Iterator
from typing import Any

import anyio
from fastapi.responses import StreamingResponse
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from app.platform.canonical import to_jsonable
from app.platform.context import Context
from app.platform.models import Base

BATCH = 1000
CHUNK_SIZE = 64 * 1024
type ExportTables = dict[str, Iterable[dict[str, Any]]]


def dump_models(
    db: Session, ctx: Context, models: Iterable[type[Base]], exclude: Iterable[str] = ()
) -> dict[str, list[dict[str, Any]]]:
    """All rows of the business for each model, as JSON-able dicts. `exclude` drops columns such as token hashes."""
    skip = set(exclude)
    out: dict[str, list[dict[str, Any]]] = {}
    for model in models:
        columns = [c.key for c in inspect(model).mapper.column_attrs if c.key not in skip]
        # Every row, streamed in batches (no cap: an export that silently drops rows would not be a full export).
        stmt = (
            select(model)
            .where(model.business_id == ctx.business_id)  # type: ignore[attr-defined]
            .execution_options(yield_per=BATCH)
        )
        out[str(model.__tablename__)] = [{c: to_jsonable(getattr(row, c)) for c in columns} for row in db.scalars(stmt)]
    return out


# dump_models remains available to callers that explicitly need eager results.
# Production export hooks use these single-pass iterators.
def stream_models(db: Session, ctx: Context, models: Iterable[type[Base]], exclude: Iterable[str] = ()) -> ExportTables:
    """Single-pass row iterators; callers must consume them before the transaction closes."""
    skip = set(exclude)
    return {str(model.__tablename__): _model_rows(db, ctx, model, skip) for model in models}


def export_tables(tables: ExportTables, *, streaming: bool) -> ExportTables:
    """Keep legacy direct hook calls eager; the download path explicitly opts into streaming."""
    return tables if streaming else {table: list(rows) for table, rows in tables.items()}


def _model_rows(db: Session, ctx: Context, model: type[Base], skip: set[str]) -> Iterable[dict[str, Any]]:
    columns = [c.key for c in inspect(model).mapper.column_attrs if c.key not in skip]
    stmt = (
        select(model)
        .where(model.business_id == ctx.business_id)  # type: ignore[attr-defined]
        .execution_options(yield_per=BATCH)
    )
    for row in db.scalars(stmt):
        yield {c: to_jsonable(getattr(row, c)) for c in columns}


def write_json_rows(destination: Any, rows: Iterable[dict[str, Any]]) -> int:
    """Incrementally write the existing indent=2 JSON array format, returning its row count."""
    count = 0

    def counted_rows() -> Iterator[dict[str, Any]]:
        nonlocal count
        for row in rows:
            count += 1
            yield row

    for chunk in iter_json_rows(counted_rows()):
        destination.write(chunk)
    return count


def iter_json_rows(rows: Iterable[dict[str, Any]]) -> Iterator[bytes]:
    """The same JSON bytes as json.dumps(list(rows), indent=2, ensure_ascii=False, sort_keys=True)."""
    encoder = json.JSONEncoder(indent=2, ensure_ascii=False, sort_keys=True)
    first = True
    yield b"["
    for row in rows:
        yield b"\n  " if first else b",\n  "
        for chunk in encoder.iterencode(row):
            yield chunk.replace("\n", "\n  ").encode("utf-8")
        first = False
    yield b"]" if first else b"\n]"


class _ZipBuffer(io.RawIOBase):
    """A non-seekable sink: zipfile emits data descriptors instead of seeking backwards."""

    def __init__(self) -> None:
        super().__init__()
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer) -> int:
        view = memoryview(data)
        self.data.extend(view)
        return view.nbytes

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = bytes(self.data)
        self.data.clear()
        return data


def stream_zip(modules: Iterable[tuple[str, ExportTables]], manifest: dict[str, Any]) -> Iterator[bytes]:
    """Stream ZIP members and populate existing manifest counts without retaining datasets or a ZIP file."""
    buffer = _ZipBuffer()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for module, tables in modules:
            counts: dict[str, int] = {}
            manifest["modules"][module] = counts
            for table, rows in tables.items():
                counts[table] = yield from _stream_table(archive, buffer, f"{module}/{table}.json", rows)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    # The endpoint delays this central directory until the audit transaction commits.
    yield buffer.drain()


def _stream_table(
    archive: zipfile.ZipFile, buffer: _ZipBuffer, name: str, rows: Iterable[dict[str, Any]]
) -> Generator[bytes, None, int]:
    count = 0

    def counted_rows() -> Iterator[dict[str, Any]]:
        nonlocal count
        for row in rows:
            count += 1
            yield row

    with archive.open(name, "w", force_zip64=True) as member:
        for chunk in iter_json_rows(counted_rows()):
            member.write(chunk)
            if len(buffer.data) >= CHUNK_SIZE:
                yield buffer.drain()
    if buffer.data:
        yield buffer.drain()
    return count


class ExportResponse(StreamingResponse):
    """Close the synchronous export generator on completion, errors and client disconnects."""

    def __init__(self, content: Generator[bytes], *, headers: dict[str, str]) -> None:
        self.export_iterator = content
        super().__init__(content, media_type="application/zip", headers=headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # The database transaction belongs to a synchronous iterator. Shield
            # its close from disconnect cancellation and keep rollback off the event loop.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.export_iterator.close)
