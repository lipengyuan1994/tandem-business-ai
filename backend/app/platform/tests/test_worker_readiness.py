"""Real worker lanes and deadline drain under blocked external handlers."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app import worker
from app.platform.registry import Registry


def test_ai_backlog_does_not_starve_mail_or_events(monkeypatch: Any, tmp_path: Any) -> None:
    release = threading.Event()
    ai_started = threading.Event()
    mail = threading.Event()
    delivered = threading.Event()
    counts = {"ai": 0, "non_ai": 0}
    lock = threading.Lock()
    w = worker.Worker(Registry(), 2, grace_seconds=1)
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT", heartbeat)
    monkeypatch.setattr(w, "_housekeeping", lambda: w.stopping.wait())

    def claim(name: str, *, lane: str) -> dict[str, str]:
        with lock:
            counts[lane] += 1
        return {"kind": "ai.slow" if lane == "ai" else "notifications.send"}

    def run(reg: Registry, job: dict[str, str]) -> bool:
        if job["kind"].startswith("ai."):
            ai_started.set()
            release.wait(3)
        else:
            mail.set()
        return True

    def deliver(reg: Registry, limit: int, **kwargs: Any) -> int:
        if mail.is_set():
            delivered.set()
            w.stopping.set()
        return 1

    monkeypatch.setattr(worker.jobs, "claim", claim)
    monkeypatch.setattr(worker.jobs, "run_job", run)
    monkeypatch.setattr(worker.events, "deliver_pending", deliver)
    thread = threading.Thread(target=w.run)
    thread.start()
    try:
        assert ai_started.wait(2)
        assert mail.wait(2) and delivered.wait(2)
        assert counts["ai"] == 1 and counts["non_ai"] >= 1
        assert heartbeat.exists()
        assert thread.is_alive()  # draining the blocked AI handler
    finally:
        release.set()
        w.stopping.set()
        thread.join(2)
    assert not thread.is_alive()


@pytest.mark.parametrize("kind", ["ai.slow", "notifications.send"])
def test_drain_waits_for_running_job_and_stops_claiming(kind: str, monkeypatch: Any, tmp_path: Any) -> None:
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    w = worker.Worker(Registry(), 2, grace_seconds=1)
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.setattr(w, "_housekeeping", lambda: w.stopping.wait())
    monkeypatch.setattr(worker.events, "deliver_pending", lambda *a, **kw: 0)
    claims = []

    def claim(name: str, *, lane: str) -> dict[str, str] | None:
        if lane == ("ai" if kind.startswith("ai.") else "non_ai"):
            claims.append(name)
            return {"kind": kind}
        return None

    def run(*args: Any) -> bool:
        entered.set()
        release.wait(3)
        completed.set()
        return True

    monkeypatch.setattr(worker.jobs, "claim", claim)
    monkeypatch.setattr(worker.jobs, "run_job", run)
    thread = threading.Thread(target=w.run)
    thread.start()
    assert entered.wait(2)
    w.stopping.set()  # same event set by the SIGTERM/SIGINT handlers
    thread.join(0.05)
    assert thread.is_alive() and not completed.is_set()
    release.set()
    thread.join(2)
    assert not thread.is_alive() and completed.is_set() and len(claims) == 1


def test_drain_uses_one_deadline_for_all_threads(monkeypatch: Any, tmp_path: Any) -> None:
    release = threading.Event()
    entered = threading.Barrier(3)
    w = worker.Worker(Registry(), 2, grace_seconds=0.1)
    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.setattr(w, "_housekeeping", lambda: w.stopping.wait())

    def blocked(*args: Any, **kwargs: Any) -> int:
        entered.wait(timeout=2)
        release.wait(3)
        return 0

    monkeypatch.setattr(worker.events, "deliver_pending", blocked)
    thread = threading.Thread(target=w.run)
    thread.start()
    entered.wait(timeout=2)
    start = time.monotonic()
    w.stopping.set()
    thread.join(0.5)
    elapsed = time.monotonic() - start
    release.set()
    assert not thread.is_alive() and elapsed < 0.3


def test_housekeeping_does_not_refresh_execution_heartbeat(monkeypatch: Any, tmp_path: Any) -> None:
    w = worker.Worker(Registry(), 2)
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT", heartbeat)
    monkeypatch.setattr(worker.jobs, "enqueue_cron", lambda reg: w.stopping.set())
    monkeypatch.setattr(worker.jobs, "reap_stale", lambda: 0)
    monkeypatch.setattr(worker.events, "reap_stale", lambda: 0)
    w._housekeeping()
    assert not heartbeat.exists()


def test_failed_event_attempt_does_not_count_as_progress(monkeypatch: Any, tmp_path: Any) -> None:
    w = worker.Worker(Registry(), 2)
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT", heartbeat)

    def failed(reg: Registry, limit: int, **kwargs: Any) -> int:
        w.stopping.set()
        return 1  # attempted, but success callback was never called

    monkeypatch.setattr(worker.events, "deliver_pending", failed)
    w._loop(0)
    assert not heartbeat.exists()
