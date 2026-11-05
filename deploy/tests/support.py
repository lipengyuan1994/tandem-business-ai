"""Isolated, synthetic command fakes for the deployment regression suite."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("SMB_TEST_ROOT", Path(__file__).resolve().parents[2]))


class OperationsCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.trace = self.work / "trace"
        self.trace.touch()
        self.env = dict(
            os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", TRACE=str(self.trace)
        )
        self.env.pop("VULTR_INFERENCE_API_KEY", None)
        self.env.pop("BACKUP_HEALTH_TIMEOUT", None)

    def stub(self, name, code):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\nimport os, sys, pathlib, json\n" + code)
        path.chmod(0o755)

    def stage(self):
        """A staged stack (as on the VM) in `self.stack`, at VERSION 1.0.0 with a smoke test that always passes."""
        result = self.run_script("stage.sh", str(self.work / "stack"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.stack = self.work / "stack"
        self.env.update(COMPOSE_DIR=str(self.stack), SKIP_PULL="1", HEALTH_TIMEOUT="1")
        (self.stack / ".env").write_text("VERSION=1.0.0\nPUBLIC_URL=https://example.com\n")
        (self.stack / "smoke.sh").write_text("#!/bin/bash\nexit 0\n")
        (self.stack / "smoke.sh").chmod(0o755)

    def run_script(self, name, *args, **kwargs):
        return subprocess.run(
            ["/bin/bash", str(ROOT / "deploy/scripts" / name), *args],
            cwd=self.work,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
            **kwargs,
        )
