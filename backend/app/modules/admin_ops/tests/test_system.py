"""M18 subset: the System page (task P6-09)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.main import create_app
from app.modules.admin_ops.service import read_backup_status
from app.modules.identity.testing import login_as
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.registry import Registry

NOW = datetime(2026, 11, 5, 17, 30, tzinfo=UTC)


@pytest.fixture
def app(registry: Registry) -> Any:
    clock.set_clock(lambda: NOW)
    return create_app(registry)


def test_only_owner_and_admin_may_read_the_system_page(app: Any) -> None:
    business = make_business()
    assert login_as(app, business, ["owner"]).get("/api/v1/admin/system").status_code == 200
    assert login_as(app, business, ["admin"]).get("/api/v1/admin/system").status_code == 200
    for role in ("operator", "inventory_manager", "marketing_manager", "auditor"):
        assert login_as(app, business, [role]).get("/api/v1/admin/system").status_code == 403


def test_reports_version_queue_age_dead_jobs_and_outbox_issues(
    app: Any, owner_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    business = make_business()
    other = make_business("example-garden-store")
    owner = login_as(app, business, ["owner"])
    from app.platform.config import get_settings

    monkeypatch.setattr(get_settings(), "app_version", "0.1.0-test")
    monkeypatch.setattr(get_settings(), "backup_status_file", str(tmp_path / "missing.json"))
    with owner_engine.begin() as conn:
        for b, status, kind, age in (
            (business, "queued", "x.slow", 600),
            (business, "dead", "x.broken", 0),
            (other, "dead", "y.other", 0),
        ):
            conn.execute(
                text(
                    "insert into jobs (id, business_id, kind, status, run_at, payload, created_at, updated_at) "
                    "values (gen_random_uuid(), :b, :k, :s, :t, '{}', now(), now())"
                ),
                {"b": b, "k": kind, "s": status, "t": NOW - timedelta(seconds=age)},
            )
    body = owner.get("/api/v1/admin/system").json()
    assert body["version"] == "0.1.0-test" and body["ai_usage_today"] is None
    assert body["queue"]["oldest_queued_age_seconds"] == 600
    assert body["queue"]["dead_jobs"] == 1 and body["queue"]["dead_kinds"] == [
        "x.broken"
    ]  # not the other business's job
    assert body["backup"]["available"] is False  # no status file: reported, not hidden
    assert body["outbox"]["needs_attention"] == 0


def test_backup_status_file_is_read_with_its_age(tmp_path: Path) -> None:
    file = tmp_path / "backup-status.json"
    file.write_text(
        json.dumps({"result": "ok", "last_success": "2026-11-05T14:30:00Z", "snapshot_id": "a1b2c3d4", "error": None}),
        encoding="utf-8",
    )
    status = read_backup_status(str(file), now=NOW)
    assert (
        status.available
        and status.result == "ok"
        and status.age_seconds == 3 * 3600
        and status.snapshot_id == "a1b2c3d4"
    )
    file.write_text("not json", encoding="utf-8")
    assert read_backup_status(str(file), now=NOW).available is False
    file.write_text(json.dumps({"result": "error", "last_success": None, "error": "boom"}), encoding="utf-8")
    failed = read_backup_status(str(file), now=NOW)
    assert failed.available and failed.result == "error" and failed.age_seconds is None and failed.error == "boom"
