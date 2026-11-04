"""Worker composition root: runs jobs, event deliveries, cron and the reaper (07-workflow-engine.md).

`python -m app.worker` — stops gracefully on SIGTERM/SIGINT. Writes a heartbeat file for health checks.
"""

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
from app.platform.logging import configure_logging
from app.platform.registry import Registry

log = structlog.get_logger(__name__)
HEARTBEAT = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))  # noqa: S108
IDLE_SLEEP = 0.5


class Worker:
    def __init__(self, registry: Registry, concurrency: int) -> None:
        self.registry = registry
        self.concurrency = concurrency
        self.name = f"{socket.gethostname()}-{os.getpid()}"
        self.stopping = threading.Event()

    def _loop(self, index: int) -> None:
        worker_name = f"{self.name}-{index}"
        while not self.stopping.is_set():
            try:
                job = jobs.claim(worker_name)
                if job is not None:
                    jobs.run_job(self.registry, job)
                    continue
                if events.deliver_pending(self.registry, limit=10):
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
                HEARTBEAT.write_text(str(time.time()))
            except Exception:
                log.exception("worker_housekeeping_error")
            self.stopping.wait(10)

    def run(self) -> None:
        threads = [threading.Thread(target=self._loop, args=(i,), daemon=True) for i in range(self.concurrency)]
        threads.append(threading.Thread(target=self._housekeeping, daemon=True))
        for t in threads:
            t.start()
        log.info("worker_started", name=self.name, concurrency=self.concurrency)
        self.stopping.wait()
        for t in threads:
            t.join(timeout=30)
        log.info("worker_stopped", name=self.name)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "dev")
    worker = Worker(build_registry(), settings.worker_concurrency)
    signal.signal(signal.SIGTERM, lambda *_: worker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopping.set())
    worker.run()


if __name__ == "__main__":
    main()
