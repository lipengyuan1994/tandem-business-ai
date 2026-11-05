"""SIGTERM through the production entry point, with synthetic external operations."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

CHILD = """
import sys, time
from pathlib import Path
from types import SimpleNamespace
from app import worker
from app.platform.registry import Registry
folder, kind = Path(sys.argv[1]), sys.argv[2]
worker.HEARTBEAT = folder / 'heartbeat'
worker.get_settings = lambda: SimpleNamespace(log_level='error', app_env='test',
    worker_concurrency=2, worker_ai_concurrency=1, worker_shutdown_grace_seconds=2)
worker.configure_engine = lambda **kw: None
worker.build_registry = Registry
worker.Worker._housekeeping = lambda self: self.stopping.wait()
worker.events.deliver_pending = lambda *a, **kw: 0
claimed = False

def claim(name, *, lane):
    global claimed
    if not claimed and lane == ('ai' if kind.startswith('ai.') else 'non_ai'):
        claimed = True
        return {'kind': kind}

def run(registry, job):
    (folder / 'started').touch()
    while not (folder / 'release').exists():
        time.sleep(.01)
    (folder / 'completed').touch()
    return True

worker.jobs.claim = claim
worker.jobs.run_job = run
worker.main()
"""


@pytest.mark.parametrize("kind", ["ai.slow", "notifications.send"])
def test_sigterm_drains_external_operation(kind: str, tmp_path: Path) -> None:
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and synthetic inline code
        [sys.executable, "-c", CHILD, str(tmp_path), kind],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={**os.environ, "WORKER_HEARTBEAT_FILE": str(tmp_path / "heartbeat")},
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "started").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "started").exists()
        process.send_signal(signal.SIGTERM)
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=0.1)
        (tmp_path / "release").touch()
        assert process.wait(timeout=3) == 0
        assert (tmp_path / "completed").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if process.stderr:
            process.stderr.close()
