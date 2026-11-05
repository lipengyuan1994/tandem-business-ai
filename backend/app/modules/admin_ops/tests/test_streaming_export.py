"""Production export streaming, exact JSON compatibility and transaction/response lifetime."""

from __future__ import annotations

import io
import json
import random
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio
import pytest
from sqlalchemy import Engine, text
from starlette.requests import ClientDisconnect

from app.main import create_app
from app.modules.identity.testing import login_as
from app.platform import clock
from app.platform.db import get_engine, transaction
from app.platform.export import ExportResponse, ExportTables
from app.platform.registry import Registry
from app.platform.testing.auth import ctx_for
from app.seed import seed

from .. import api, permissions, service
from .test_data import CYCLE, NOW


def test_download_matches_every_legacy_hook_byte_for_byte_and_keeps_audit(
    registry: Registry, owner_engine: Engine
) -> None:
    clock.set_clock(lambda: NOW)
    seed()
    owner = login_as(create_app(registry), CYCLE, ["owner"])
    ctx = ctx_for(CYCLE, [permissions.DATA_MANAGE])
    expected: dict[str, bytes] = {}
    counts: dict[str, dict[str, int]] = {}
    with transaction(ctx) as db:
        for module, handler in registry.export_handlers.items():
            tables = handler(db, ctx)
            counts[module] = {}
            for table, rows in tables.items():
                assert isinstance(rows, list)  # legacy hook contract, including access after the transaction
                expected[f"{module}/{table}.json"] = json.dumps(
                    rows, indent=2, ensure_ascii=False, sort_keys=True
                ).encode()
                counts[module][table] = len(rows)
    response = owner.post("/api/v1/admin/exports")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == 'attachment; filename="export-2026-11-05.zip"'
    assert response.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {*expected, "manifest.json"}
        for name, data in expected.items():
            assert archive.read(name) == data, name
        manifest = {"format_version": 1, "exported_at": NOW.isoformat(), "business_id": str(CYCLE), "modules": counts}
        assert archive.read("manifest.json") == json.dumps(manifest, indent=2, sort_keys=True).encode()
    with owner_engine.connect() as db:
        row = db.execute(
            text("""select business_id, actor_kind, actor_id, target_type, target_id, summary, changes
            from audit_events where action='admin_ops.export.created'""")
        ).one()
        assert row.business_id == CYCLE and row.actor_kind == "user" and row.actor_id is not None
        assert row.target_type == "business" and row.target_id == CYCLE
        assert row.summary == "Full data export downloaded"
        assert row.changes == {"tables": sum(len(tables) for tables in counts.values())}
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]


def test_all_production_hooks_return_row_iterators(registry: Registry) -> None:
    clock.set_clock(lambda: NOW)
    seed()
    with transaction(ctx_for(CYCLE)) as db:
        for module, handler in registry.export_handlers.items():
            tables = handler(db, ctx_for(CYCLE), streaming=True)
            for table, rows in tables.items():
                assert not isinstance(rows, list), f"{module}/{table} materialized its rows"
                for _ in rows:
                    pass


@pytest.mark.parametrize("asgi_version", ["2.3", "2.4"])
def test_disconnect_closes_export_and_rolls_back_open_transaction(
    business: Any, registry: Registry, owner_engine: Engine, asgi_version: str
) -> None:
    closed: list[bool] = []
    produced = 0

    def rows() -> Iterator[dict[str, Any]]:
        nonlocal produced
        random_bytes = random.Random(7)
        try:
            for _ in range(10_000):
                produced += 1
                yield {"value": random_bytes.randbytes(1024).hex()}
        finally:
            closed.append(True)

    def hook(db: Any, ctx: Any, *, streaming: bool = False) -> ExportTables:
        return {"rows": rows()}

    registry.export_handlers = {"test": hook}
    response = ExportResponse(api._export_chunks(ctx_for(business, [permissions.DATA_MANAGE]), NOW), headers={})

    async def scenario() -> None:
        body_sent = anyio.Event()

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body":
                body_sent.set()
                if asgi_version == "2.4":
                    raise OSError("synthetic client disconnect")
                await anyio.sleep_forever()  # backpressure until receive reports disconnect

        async def receive() -> Any:
            await body_sent.wait()
            return {"type": "http.disconnect"}

        scope = {"type": "http", "asgi": {"spec_version": asgi_version}}
        if asgi_version == "2.4":
            with pytest.raises(ClientDisconnect):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)

    anyio.run(scenario)
    assert closed == [True] and 0 < produced < 10_000
    assert response.export_iterator.gi_frame is None
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from audit_events where action='admin_ops.export.created'")) == 0


def test_audit_failure_withholds_zip_central_directory(
    business: Any, registry: Registry, owner_engine: Engine, monkeypatch: Any
) -> None:
    def hook(db: Any, ctx: Any, *, streaming: bool = False) -> ExportTables:
        return {"rows": iter([{"synthetic": "data"}])}

    registry.export_handlers = {"test": hook}
    real_transaction = transaction

    @contextmanager
    def reject_commit(ctx: Any) -> Iterator[Any]:
        with real_transaction(ctx) as db:
            yield db
            raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(api, "transaction", reject_commit)
    chunks: list[bytes] = []
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        for chunk in api._export_chunks(ctx_for(business, [permissions.DATA_MANAGE]), NOW):
            chunks.append(chunk)
    assert chunks and b"PK\x05\x06" not in b"".join(chunks)
    with owner_engine.connect() as db:
        assert db.scalar(text("select count(*) from audit_events where action='admin_ops.export.created'")) == 0


def test_service_still_requires_owner_export_permission(business: Any, registry: Registry) -> None:
    from app.platform.errors import Forbidden

    with transaction(ctx_for(business)) as db:
        with pytest.raises(Forbidden):
            next(service.build_export(db, ctx_for(business), exported_at=NOW))
