from __future__ import annotations

import io
import json
import tempfile
import tracemalloc
from collections.abc import Iterator
from typing import Any
from uuid import UUID

from app.platform.db import transaction
from app.platform.export import dump_models, stream_models, write_json_rows
from app.platform.models import Job
from app.platform.testing.auth import ctx_for


def test_streamed_json_is_byte_identical() -> None:
    rows = [{"z": "你好", "nested": {"a": [1, 2], "b": None}, "enabled": True}, {"a": "x\ny"}]
    for values in ([], rows):
        output = io.BytesIO()
        assert write_json_rows(output, iter(values)) == len(values)
        assert output.getvalue() == json.dumps(values, indent=2, ensure_ascii=False, sort_keys=True).encode()


def test_row_writer_does_not_accumulate_dataset() -> None:
    def rows() -> Iterator[dict[str, Any]]:
        for index in range(10_000):
            yield {"index": index, "body": f"{index}:" + "x" * 2048}

    with tempfile.TemporaryFile() as output:
        tracemalloc.start()
        try:
            assert write_json_rows(output, rows()) == 10_000
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert output.tell() > 20_000_000
        assert peak < 2_000_000


def test_model_rows_are_lazy_and_keep_tenant_filter(business: UUID, other_business: UUID) -> None:
    with transaction(ctx_for(business)) as db:
        db.add(Job(kind="test.mine", business_id=business))
        db.add(Job(kind="test.other", business_id=other_business))
    with transaction(ctx_for(business)) as db:
        eager = dump_models(db, ctx_for(business), [Job])
        streaming = stream_models(db, ctx_for(business), [Job])
        assert not isinstance(streaming["jobs"], list)
        assert list(streaming["jobs"]) == eager["jobs"]
        assert [row["kind"] for row in eager["jobs"]] == ["test.mine"]


def test_zip_members_and_manifest_keep_exact_json_bytes() -> None:
    import zipfile

    from app.platform.export import stream_zip

    tables = {"items": [{"name": "商品", "nested": {"list": [1, 2]}, "empty": {}}, {"line": "a\nb"}], "empty": []}
    manifest = {"format_version": 1, "exported_at": "2026-11-03", "business_id": "synthetic", "modules": {}}
    data = b"".join(stream_zip([("test", {key: iter(rows) for key, rows in tables.items()})], manifest))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for table, rows in tables.items():
            assert (
                archive.read(f"test/{table}.json")
                == json.dumps(rows, indent=2, ensure_ascii=False, sort_keys=True).encode()
            )
        assert manifest["modules"] == {"test": {"items": 2, "empty": 0}}
        assert archive.read("manifest.json") == json.dumps(manifest, indent=2, sort_keys=True).encode()


def test_zip_stream_uses_bounded_memory_and_starts_before_all_rows_are_read() -> None:
    import random

    from app.platform.export import CHUNK_SIZE, stream_zip

    consumed = 0
    random_bytes = random.Random(41)

    def rows() -> Iterator[dict[str, Any]]:
        nonlocal consumed
        for index in range(10_000):
            consumed += 1
            yield {"index": index, "body": random_bytes.randbytes(2048).hex()}

    manifest: dict[str, Any] = {"modules": {}}
    stream = stream_zip([("test", {"large": rows()})], manifest)
    assert consumed == 0
    tracemalloc.start()
    try:
        first = next(stream)
        assert first.startswith(b"PK") and 0 < consumed < 1000
        size, largest = len(first), len(first)
        for chunk in stream:
            size += len(chunk)
            largest = max(largest, len(chunk))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        stream.close()
    assert size > 20_000_000  # compressed, deliberately poorly compressible data
    assert largest < CHUNK_SIZE * 2
    assert peak < 3_000_000
    assert manifest["modules"] == {"test": {"large": 10_000}}
