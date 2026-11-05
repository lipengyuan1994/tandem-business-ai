"""Bounded AI and operational lanes, fair event delivery and deadline-based drain."""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from pathlib import Path

import structlog

from app.main import build_registry
from app.platform import events, jobs
from app.platform.config import get_settings
from app.platform.db import configure_engine
from app.platform.logging import configure_logging
from app.platform.registry import Registry

log = structlog.get_logger(__name__)
HEARTBEAT = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))  # noqa: S108
IDLE_SLEEP = 0.5


class Worker:
    def __init__(
        self, registry: Registry, concurrency: int, *, ai_concurrency: int | None = None, grace_seconds: float = 100
    ) -> None:
        if concurrency < 2:
            raise ValueError("worker needs at least two execution threads")
        self.registry = registry
        self.concurrency = concurrency
        self.ai_concurrency = ai_concurrency if ai_concurrency is not None else min(2, concurrency - 1)
        if not 1 <= self.ai_concurrency < concurrency:
            raise ValueError("AI capacity must leave at least one operational thread")
        self.grace_seconds = grace_seconds
        self.name = f"{socket.gethostname()}-{os.getpid()}"
        self.stopping = threading.Event()
        self._heartbeat_lock = threading.Lock()

    def _progress(self) -> None:
        # Housekeeping alone cannot make a stuck executor look healthy. Idle polls
        # also count: they prove an execution lane can still reach the database.
        with self._heartbeat_lock:
            HEARTBEAT.write_text(str(time.time()))

    def _loop(self, index: int) -> None:
        worker_name = f"{self.name}-{index}"
        lane = "ai" if index < self.ai_concurrency else "non_ai"
        while not self.stopping.is_set():
            try:
                # One event then one job every turn: neither continuous jobs nor
                # continuous events can starve the other queue.
                delivered = events.deliver_pending(self.registry, limit=1, on_progress=self._progress)
                if self.stopping.is_set():
                    break
                job = jobs.claim(worker_name, lane=lane)
                if job is not None:
                    if jobs.run_job(self.registry, job):
                        self._progress()
                    continue
                if not delivered:
                    self._progress()
                else:
                    continue
            except Exception:
                log.exception("worker_loop_error")
            self.stopping.wait(IDLE_SLEEP)

    def _housekeeping(self) -> None:
        while not self.stopping.is_set():
            try:
                jobs.enqueue_cron(self.registry)
                jobs.reap_stale()
                events.reap_stale()
            except Exception:
                log.exception("worker_housekeeping_error")
            self.stopping.wait(10)

    def run(self) -> None:
        threads = [threading.Thread(target=self._loop, args=(i,), daemon=True) for i in range(self.concurrency)]
        threads.append(threading.Thread(target=self._housekeeping, daemon=True))
        for thread in threads:
            thread.start()
        log.info("worker_started", name=self.name, concurrency=self.concurrency, ai_concurrency=self.ai_concurrency)
        self.stopping.wait()
        deadline = time.monotonic() + self.grace_seconds
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        unfinished = sum(thread.is_alive() for thread in threads)
        if unfinished:
            log.warning("worker_drain_timeout", name=self.name, unfinished_threads=unfinished)
        else:
            log.info("worker_stopped", name=self.name)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "dev")
    configure_engine(process="worker")
    worker = Worker(
        build_registry(),
        settings.worker_concurrency,
        ai_concurrency=settings.worker_ai_concurrency,
        grace_seconds=settings.worker_shutdown_grace_seconds,
    )
    signal.signal(signal.SIGTERM, lambda *_: worker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopping.set())
    worker.run()


if __name__ == "__main__":
    main()
