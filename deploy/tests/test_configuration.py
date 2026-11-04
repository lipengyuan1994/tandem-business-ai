"""Deployment configuration regressions that need no running services."""

import json
import unittest

from support import ROOT


class ConfigurationTests(unittest.TestCase):
    def test_uvicorn_access_logging_is_disabled(self):
        text = (ROOT / "deploy/docker/api.Dockerfile").read_text()
        cmd = json.loads(
            next(line[4:] for line in text.splitlines() if line.startswith("CMD "))
        )
        self.assertIn("--no-access-log", cmd)

    def test_public_body_limit_and_bounded_proxy_buffer(self):
        config = (ROOT / "deploy/compose/Caddyfile").read_text()
        self.assertRegex(
            config, r"request_body /api/v1/public/\*\s*\{\s*max_size 128KB\s*\}"
        )
        self.assertRegex(
            config,
            r"reverse_proxy /api/v1/public/\* api:8000\s*\{\s*request_buffers 128KB\s*\}",
        )
        self.assertIn("int({http.request.header.Content-Length}) > 128000", config)
        self.assertIn('respond @oversized_public "Request body too large" 413', config)

    def test_web_updates_packages_and_removes_unused_curl(self):
        text = (
            (ROOT / "deploy/docker/web.Dockerfile")
            .read_text()
            .split("AS runtime", 1)[1]
        )
        self.assertIn("apk upgrade --no-cache", text)
        self.assertIn("apk del --no-cache curl", text)
        self.assertIn('CMD ["wget",', text)
        self.assertNotIn('CMD ["curl",', text)

    def test_regulated_runbook_uses_staged_compose_name(self):
        runbook = (ROOT / "docs/runbooks/regulated-profile.md").read_text()
        self.assertIn(
            "-f compose.yml up -d --no-deps --force-recreate api worker", runbook
        )
        self.assertIn(
            '"$dest/compose.yml"', (ROOT / "deploy/scripts/stage.sh").read_text()
        )

    def test_dev_worker_reloads_on_python_changes_and_production_does_not(self):
        dev = (ROOT / "deploy/compose/compose.dev.yml").read_text()
        worker = dev.split("\n  worker:\n", 1)[1].split("\n  web:\n", 1)[0]
        command = next(
            line for line in worker.splitlines() if line.strip().startswith("command:")
        )
        self.assertIn("watchfiles", command)
        self.assertIn("--filter python", command)
        self.assertIn("'python -m app.worker' /app/app", command)
        prod = (ROOT / "deploy/compose/compose.prod.yml").read_text()
        self.assertNotIn("watchfiles", prod)

    def test_rehearsal_restarts_after_applied_migration_rollback(self):
        text = (ROOT / "deploy/scripts/rehearse.sh").read_text()
        after_rollback = text.split(
            '[ "$applied" = 20261026_0900_rehearsal_probe ]', 1
        )[1]
        self.assertIn('"${compose[@]}" down', after_rollback)
        self.assertIn('"${compose[@]}" up -d --wait --wait-timeout 300', after_rollback)
        self.assertNotIn("docker rmi -f smb/smb-api:0.1.0-rc3", after_rollback)
