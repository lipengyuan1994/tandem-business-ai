"""Backup health synchronization and route validation, using only local files and command fakes."""

import os
import shutil
import subprocess

from support import ROOT, OperationsCase


class BackupHealthTests(OperationsCase):
    def fake_compose(self):
        self.stub("curl", "sys.exit('unexpected HTTP request in offline test')\n")
        self.stub("docker", """
a = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as f: f.write(' '.join(a) + '\\n')
if 'ps' in a:
    if a[-1] == 'backup':
        path = pathlib.Path(os.environ['TRACE'] + '.polls')
        n = int(path.read_text()) + 1 if path.exists() else 1
        path.write_text(str(n))
        value = os.environ.get('HEALTH_RESULT', 'backup|healthy')
        if n <= int(os.environ.get('UNHEALTHY_POLLS', '0')): value = 'backup|unhealthy'
        print(value)
        if os.environ.get('PS_FAIL'): sys.exit(1)
    else: print('api|Up (healthy)\\nbackup|Up (healthy)')
elif 'backup.sh' in a and os.environ.get('BACKUP_FAIL'): sys.exit(17)
elif 'psql' in a:
    sys.stdin.read()
    print('2')
""")

    def trace_smoke(self):
        (self.stack / "smoke.sh").write_text('#!/bin/bash\necho SMOKE >> "$TRACE"\n')

    def run_staged(self, name, *args):
        return subprocess.run(["/bin/bash", str(self.stack / name), *args], cwd=self.stack,
                              env=self.env, text=True, capture_output=True, check=False, timeout=15)

    def test_wait_uses_backup_service_health_and_keeps_compose_options(self):
        self.fake_compose()
        self.env["UNHEALTHY_POLLS"] = "2"
        self.stub("sleep", "pass\n")
        result = self.run_script("wait-backup-healthy.sh", "docker", "compose", "-f", "custom.yml")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.trace.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all("compose -f custom.yml ps --all --format {{.Service}}|{{.Health}} backup" == line for line in lines))

    def test_timeout_rejects_unhealthy_missing_and_failed_status(self):
        self.fake_compose()
        self.env["BACKUP_HEALTH_TIMEOUT"] = "1"
        for status, failed in (("backup|unhealthy", ""), ("backup|starting", ""),
                               ("", ""), ("backup|", ""), ("api|healthy", ""),
                               ("backup|healthy", "1")):
            with self.subTest(status=status, command_failed=failed):
                self.env.update(HEALTH_RESULT=status, PS_FAIL=failed)
                result = self.run_script("wait-backup-healthy.sh", "docker", "compose")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not healthy after 1s", result.stderr)

    def test_invalid_timeout_is_rejected_without_polling(self):
        self.fake_compose()
        for value in ("0", "-1", "garbage"):
            with self.subTest(value=value):
                self.env["BACKUP_HEALTH_TIMEOUT"] = value
                result = self.run_script("wait-backup-healthy.sh", "docker", "compose")
                self.assertEqual(result.returncode, 64)
                self.assertEqual(self.trace.read_text(), "")

    def test_upgrade_waits_before_changes_and_preserves_operator_routes(self):
        self.stage()
        self.trace_smoke()
        self.fake_compose()
        self.stub("sleep", "pass\n")
        self.env["UNHEALTHY_POLLS"] = "2"
        route = self.stack / "config/ai-routes.yaml"
        route.write_text("# operator config, keep verbatim\nproviders: {}\nroutes: {}\n")
        original = route.read_bytes()
        result = self.run_staged("upgrade.sh", "2.0.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(route.read_bytes(), original)
        trace = self.trace.read_text()
        self.assertEqual(trace.count("{{.Health}} backup"), 3)
        self.assertLess(trace.index("backup.sh --kind pre-upgrade"), trace.index("{{.Health}} backup"))
        self.assertLess(trace.rindex("{{.Health}} backup"), trace.index("run --rm -T migrate"))
        self.assertLess(trace.index("up -d"), trace.index("SMOKE"))

    def test_upgrade_health_timeout_leaves_version_and_skips_smoke(self):
        self.stage()
        self.trace_smoke()
        self.fake_compose()
        self.env.update(BACKUP_HEALTH_TIMEOUT="1", HEALTH_RESULT="backup|unhealthy")
        result = self.run_staged("upgrade.sh", "2.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VERSION=1.0.0\n", (self.stack / ".env").read_text())
        self.assertNotIn("SMOKE", self.trace.read_text())
        self.assertNotIn("up -d", self.trace.read_text())

    def test_restore_waits_after_startup_before_smoke(self):
        self.stage()
        self.trace_smoke()
        self.fake_compose()
        self.stub("sleep", "pass\n")
        self.env["UNHEALTHY_POLLS"] = "2"
        result = self.run_staged("restore.sh", "synthetic", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        trace = self.trace.read_text()
        self.assertEqual(trace.count("{{.Health}} backup"), 3)
        self.assertLess(trace.index("up -d"), trace.index("{{.Health}} backup"))
        self.assertLess(trace.rindex("{{.Health}} backup"), trace.index("SMOKE"))

    def test_restore_health_timeout_never_runs_smoke(self):
        self.stage()
        self.trace_smoke()
        self.fake_compose()
        self.env.update(BACKUP_HEALTH_TIMEOUT="1", HEALTH_RESULT="backup|unhealthy")
        result = self.run_staged("restore.sh", "synthetic", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SMOKE", self.trace.read_text())

    def test_rehearsal_waits_before_first_smoke_and_helper_is_staged(self):
        self.stage()
        self.assertTrue(os.access(self.stack / "wait-backup-healthy.sh", os.X_OK))
        # Rehearsal builds/runs containers; check its ordering without starting that workflow.
        script = (ROOT / "deploy/scripts/rehearse.sh").read_text()
        self.assertLess(script.index('"$dir/wait-backup-healthy.sh"'), script.index('"$dir/smoke.sh"'))


class FallbackRouteValidationTests(OperationsCase):
    def test_without_venv_parses_yaml_and_requires_provider_and_route_mappings(self):
        # Use the project's already installed YAML parser with a copy that has no backend venv.
        python = ROOT / "backend/.venv/bin/python"
        if not python.is_file():
            self.skipTest("backend venv unavailable for the existing PyYAML parser")
        scripts = self.work / "copy/deploy/scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / "deploy/scripts/install.sh", scripts / "install.sh")
        shutil.copy2(ROOT / "deploy/scripts/install-ai.sh", scripts / "install-ai.sh")
        self.stub("python3", f"os.execv({str(python)!r}, [{str(python)!r}, *sys.argv[1:]])\n")
        args = ["--dry-run", "--demo", "--domain", "auto", "--name", "demo",
                "--registry", "r.example.com/smb", "--registry-user", "synthetic",
                "--timezone", "UTC", "--object-storage-url", "https://storage.example.com",
                "--bucket", "synthetic", "--ai-routes", str(self.work / "routes.yaml")]
        for content, expected in (("providers: {}\nroutes: {}\n", 0), ("providers: {}\nroutes: [\n", 64),
                                  ("providers: {}\n", 64), ("providers: []\nroutes: {}\n", 64)):
            with self.subTest(content=content):
                (self.work / "routes.yaml").write_text(content)
                result = subprocess.run(["/bin/bash", str(scripts / "install.sh"), *args],
                                        cwd=self.work, env=self.env, text=True, capture_output=True, check=False, timeout=15)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertFalse((self.work / "install-demo").exists())
